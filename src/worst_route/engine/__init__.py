"""The engine: routes, their only mutation, and the proof that a route is legal.

`route` holds `Route`, whose `splice()` is the only way a route changes and refuses anything
that would repeat a node, an edge, a twin, a banned transition or a stop; `validator` proves a
finished route legal from the raw data, independently of `route`. `request` holds the request
and the masks it implies; `snap` puts the stops on nodes; `seed_route` builds the baseline and
the seed route the loop starts from; `objectives` holds the `Objective` interface and the modes,
and `constraints` the turn constraints a request adds to them;
`target` holds a request's target and the goal the loop pursues; `turns` classes each turn
and counts a route's; `detour` finds replacements for a window of a route; `worsen` is the loop
that splices them in; `plan` runs it all for a request and validates the result. How the
engine works: `docs/reference/engine.md`; the design: `docs/PLAN.md` §3.

`ENGINE_VERSION` is recorded with every route: bump it when the same build, request and seed
would give a different route.
"""

ENGINE_VERSION = "4"  # 4: a node-simple path searched for (config.SIMPLE_RETRY_ROUNDS)
