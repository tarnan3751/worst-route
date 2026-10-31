"""Who a client is, and what a server allows it (`docs/reference/api.md`, **Public limits**).

`Limits` is what a server allows: `LOCAL`, the CLI's and a local server's (a budget up to
`BUDGET_MAX_S`, nothing per client), or `PUBLIC`, a server run with `serve --public` (the
`config.PUBLIC_*` numbers: a budget up to 60 s, one job running and three waiting a client, ten
place searches and sixty picks of a build (`POST /regions/pick`) a minute a client). A request's
own leash is `LEASH_MAX_KM` at most in both, as the request's schema has it; the automatic leash
follows the trip (a fifth of it beyond 750 km) and the budget bounds the job. The job manager
counts each client's jobs (`JobSettings.running_per_client`, `queued_per_client`); `GeocodeQuota`
counts its searches, and its picks apart, over a sliding minute.

`client_of(scope, trust_proxy)` names the client of a request: its peer's address, or behind a
reverse proxy (`ApiSettings.trust_proxy`) the last address of `X-Forwarded-For`, the one that
proxy appended (the earlier ones are whatever the client sent, so never trusted), without a
port a proxy may append (`a.b.c.d:port`, `[v6]:port`). An IPv6 client is named by its /64, the
block one subscriber usually holds. `client_hash` is what the
logs show instead: a sha256 of the name under a salt drawn when the process starts, so a
client's lines can be followed within one run of the server and its address is never written.
"""

import hashlib
import ipaddress
import math
import secrets
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from starlette.datastructures import Headers
from starlette.types import Scope

from worst_route.config import (
    BUDGET_MAX_S,
    LEASH_MAX_KM,
    PUBLIC_BUDGET_MAX_S,
    PUBLIC_GEOCODE_PER_MINUTE,
    PUBLIC_PICK_PER_MINUTE,
    PUBLIC_QUEUED_PER_CLIENT,
    PUBLIC_RUNNING_PER_CLIENT,
)

_SALT = secrets.token_bytes(16)
MINUTE_S = 60.0


@dataclass(frozen=True)
class Limits:
    """What a server allows a request and a client (None: no limit)."""

    budget_max_s: float
    leash_max_km: float
    running_per_client: int | None
    queued_per_client: int | None
    geocode_per_minute: int | None
    pick_per_minute: int | None


LOCAL = Limits(BUDGET_MAX_S, LEASH_MAX_KM, None, None, None, None)
PUBLIC = Limits(
    PUBLIC_BUDGET_MAX_S,
    LEASH_MAX_KM,
    PUBLIC_RUNNING_PER_CLIENT,
    PUBLIC_QUEUED_PER_CLIENT,
    PUBLIC_GEOCODE_PER_MINUTE,
    PUBLIC_PICK_PER_MINUTE,
)


class GeocodeQuota:
    """At most `per_minute` place searches a client in any minute, on `clock` (seconds)."""

    def __init__(self, per_minute: int, clock: Callable[[], float] = time.monotonic) -> None:
        self.per_minute = per_minute
        self._clock = clock
        self._times: dict[str, deque[float]] = {}  # each client's searches within the minute
        self._swept = clock()

    def take(self, client: str) -> int | None:
        """Count a search by `client` and return None, or, when it has had its share of the
        last minute, count nothing and return the whole seconds until it may search again."""
        now = self._clock()
        since = now - MINUTE_S
        if self._swept <= since:  # forget the clients idle for a minute, once a minute
            self._times = {c: t for c, t in self._times.items() if t and t[-1] > since}
            self._swept = now
        times = self._times.setdefault(client, deque())
        while times and times[0] <= since:
            times.popleft()
        if len(times) >= self.per_minute:
            return max(1, math.ceil(times[0] - since))
        times.append(now)
        return None


def client_of(scope: Scope, trust_proxy: bool) -> str:
    """The client of the request `scope` describes (an ASGI HTTP scope)."""
    client = scope.get("client")
    host = client[0] if client else "unknown"
    if trust_proxy:
        forwarded = ",".join(Headers(scope=scope).getlist("x-forwarded-for"))
        last = forwarded.rsplit(",", 1)[-1].strip()
        if last:
            host = _without_port(last)
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            return str(address.ipv4_mapped)
        return str(ipaddress.IPv6Network(f"{address}/64", strict=False))
    return str(address)


def client_hash(client: str) -> str:
    """The client as the logs show it: 12 hex digits, stable within this process."""
    return hashlib.sha256(_SALT + client.encode()).hexdigest()[:12]


def _without_port(host: str) -> str:
    """`host` without a port: `a.b.c.d:port` (one colon) and `[v6]:port` (brackets); a bare
    IPv6 address has more than one colon and no brackets."""
    if host.startswith("[") and "]" in host:
        return host[1 : host.index("]")]
    if host.count(":") == 1:
        return host.split(":", 1)[0]
    return host
