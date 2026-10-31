"""The directions endpoint (`docs/reference/api.md`; `docs/reference/presentation.md`,
**Directions**).

    GET /routes/{id}/directions    {route_id, directions}: the route's turn-by-turn directions

The directions are the ones the route's document has (`routes_api.directions_of`), worked out
again from its build, its stops' labels and its seed, its jokes included; a route that is not
stored is 404 `NOT_FOUND`, and one whose build is gone 409 `BUILD_UNAVAILABLE`, as the other
route endpoints answer.
"""

from fastapi import APIRouter, Request

from worst_route.api.routes_api import api_of, build_of, directions_of, stored_route
from worst_route.api.schemas import DirectionsOut, DirectionStep

router = APIRouter()


@router.get("/routes/{route_id}/directions")
def route_directions(route_id: str, request: Request) -> DirectionsOut:
    """A stored route's directions alone."""
    api = api_of(request)
    stored = stored_route(api, route_id)
    steps = directions_of(build_of(api, stored).data, stored)
    return DirectionsOut(
        route_id=stored.route_id, directions=[DirectionStep(**s.as_dict()) for s in steps]
    )
