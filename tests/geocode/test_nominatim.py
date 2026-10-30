"""The Nominatim client: the request it sends, how it reads jsonv2, and its failures.

The bodies are under tests/fixtures/geocode/. `nominatim-docs-example.json` is the jsonv2 example
of Nominatim's search documentation (docs/api/Search.md, "JSON with address details",
bakery in berlin wedding), verbatim: the field names and types as Nominatim documents them
(`category` for json's `class`, `lat` and `lon` as strings). `nominatim-portland.json` is
hand-written in that shape for three Portland places: its ids are placeholders and its
coordinates approximate. No test here reaches Nominatim: every geocoder gets a client on an
`httpx.MockTransport` (`support.offline_clients` for the one it makes itself).
"""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from worst_route.config import GEOCODE_TIMEOUT_S
from worst_route.geocode.base import Bbox, Candidate, GeocodeError, GeocodeRefusedError
from worst_route.geocode.nominatim import NominatimGeocoder, user_agent

from .conftest import PORTLAND, POWELLS
from .support import offline_clients

pytestmark = pytest.mark.anyio

FIXTURES = Path(__file__).parents[1] / "fixtures" / "geocode"
PORTLAND_BODY = json.loads((FIXTURES / "nominatim-portland.json").read_text())
DOCS_BODY = json.loads((FIXTURES / "nominatim-docs-example.json").read_text())
BERLIN: Bbox = (13.0, 52.3, 13.8, 52.7)
UA = "worst-route/0.1.0 (https://github.com/tarnan3751/worst-route; ops@example.org)"

Handler = Callable[[httpx.Request], httpx.Response]


def geocoder(handler: Handler, base_url: str = "https://nominatim.test") -> NominatimGeocoder:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return NominatimGeocoder(base_url, UA, client=client)


def answering(body: Any, status: int = 200) -> Handler:
    return lambda request: httpx.Response(status, json=body)


async def test_one_request_with_every_parameter_and_the_user_agent() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=PORTLAND_BODY)

    await geocoder(handler, "https://nominatim.test/").search("powell's books", PORTLAND, 5)
    (request,) = seen
    assert request.method == "GET"
    assert (request.url.scheme, request.url.host, request.url.path) == (
        "https",
        "nominatim.test",
        "/search",
    )
    assert sorted(request.url.params.multi_items()) == sorted(
        [
            ("format", "jsonv2"),
            ("q", "powell's books"),
            ("limit", "5"),
            ("countrycodes", "us"),
            ("viewbox", "-123.05,45.25,-122.35,45.72"),
            ("bounded", "1"),
            ("addressdetails", "0"),
        ]
    )
    assert request.headers["user-agent"] == UA
    assert set(request.extensions["timeout"].values()) == {GEOCODE_TIMEOUT_S}


async def test_jsonv2_places_become_candidates_in_their_order() -> None:
    candidates = await geocoder(answering(PORTLAND_BODY)).search("powell's", PORTLAND, 5)
    assert candidates[0] == POWELLS
    assert [c.category for c in candidates] == ["shop/books", "shop/books", "amenity/cafe"]
    assert candidates[2] == Candidate(
        "Powell's Coffee Room, West Burnside Street, Pearl District, Portland, Multnomah County, "
        "Oregon, 97209, United States",
        45.52308,
        -122.68165,
        "amenity/cafe",
    )
    assert len(await geocoder(answering(PORTLAND_BODY)).search("powell's", PORTLAND, 2)) == 2


async def test_the_documented_example_parses_and_places_outside_the_bbox_are_dropped() -> None:
    (ditsch,) = await geocoder(answering(DOCS_BODY)).search("bakery in berlin wedding", BERLIN, 5)
    assert ditsch == Candidate(
        "Ditsch, Lindower Straße, Sprengelkiez, Wedding, Mitte, Berlin, 13347, Deutschland",
        52.54274275,
        13.36690305710228,
        "shop/bakery",
    )
    assert await geocoder(answering(DOCS_BODY)).search("ditsch", PORTLAND, 5) == []
    mixed = [*DOCS_BODY, *PORTLAND_BODY]
    found = await geocoder(answering(mixed)).search("powell's", PORTLAND, 5)
    assert [c.label.split(",")[0] for c in found] == [
        "Powell's City of Books",
        "Powell's Books on Hawthorne",
        "Powell's Coffee Room",
    ]


def raising(error: type[httpx.TransportError]) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        raise error("no", request=request)

    return handler


def place(**changes: Any) -> list[dict[str, Any]]:
    item = {**PORTLAND_BODY[0], **changes}
    return [{k: v for k, v in item.items() if v is not ...}]


@pytest.mark.parametrize(
    "handler",
    [
        answering({"error": "internal"}, 500),
        raising(httpx.ReadTimeout),
        raising(httpx.ConnectError),
        lambda request: httpx.Response(200, text="<html>Bad Gateway</html>"),
        answering({"error": "Unable to geocode"}),
        answering(["powell's"]),
        answering(place(lat=...)),
        answering(place(lat="north")),
        answering(place(lon=None)),
        answering(place(display_name=None)),
        answering(place(category=...)),
    ],
    ids=[
        "500",
        "timeout",
        "connect error",
        "not json",
        "an object",
        "not places",
        "no lat",
        "lat not a number",
        "lon null",
        "label null",
        "no category",
    ],
)
async def test_a_failure_upstream_is_a_geocode_error(handler: Handler) -> None:
    with pytest.raises(GeocodeError) as failed:
        await geocoder(handler).search("powell's", PORTLAND, 5)
    assert not isinstance(failed.value, GeocodeRefusedError)


@pytest.mark.parametrize("status", [429, 403])
async def test_too_many_requests_or_blocked_is_a_refusal(status: int) -> None:
    with pytest.raises(GeocodeRefusedError, match=f"Nominatim answered {status}"):
        await geocoder(answering([], status)).search("powell's", PORTLAND, 5)


async def test_aclose_closes_only_a_client_of_its_own(monkeypatch: pytest.MonkeyPatch) -> None:
    given = httpx.AsyncClient(transport=httpx.MockTransport(answering([])))
    await NominatimGeocoder("https://nominatim.test", UA, client=given).aclose()
    assert not given.is_closed
    made = offline_clients(monkeypatch)
    own = NominatimGeocoder("https://nominatim.test", UA)
    assert made == [own.client]
    await own.aclose()
    assert own.client.is_closed


def test_the_contact_goes_inside_the_user_agent_s_parentheses() -> None:
    base = "worst-route/0.1.0 (https://github.com/tarnan3751/worst-route)"
    assert user_agent(base, None) == base
    assert user_agent(base, "") == base
    assert user_agent(base, "ops@example.org") == UA
    assert user_agent("worst-route/0.1.0", "ops@example.org") == (
        "worst-route/0.1.0 (ops@example.org)"
    )
