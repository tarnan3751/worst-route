# Worst Route

The least efficient way to get anywhere in the US. Set a start, an end and some stops; get a
route that never repeats an intersection or a road and is as bad as the time budget allows.

It is a joke that holds: every route is proved legal by a validator before it is shown, and a
seed gives the same route on the same build forever. The pipeline turns an OpenStreetMap state
extract into a cleaned, memory-mapped road graph (one-way streets, divided highways, interchanges
and turn restrictions included); the engine is a splice loop under a time budget, with modes for
the longest route, the slowest, no left (or right) turns, collecting bridges or roundabouts, brand
pilgrimages, alphabet routes, hard mode and the worst order of the stops, and the certified worst:
in a small area, the provably worst route, proven by a solver; the API streams the
route as it grows over Server-Sent Events; the MapLibre frontend draws it with deadpan directions,
a share image, a route of the day, a draw-it-yourself mode played against the engine, and
challenges. Builds exist for Portland, Oregon and the contiguous US at primary roads and above,
and a desktop app packs the national build for macOS and Windows.

## Running it

Build Portland with `uv run worst-route pipeline <step> --region portland` for each step from
`download` to `qa` (the extract comes from Geofabrik), start the API with
`uv run worst-route serve --region portland`, and the map with `cd frontend && npm ci && npm run dev`.
`CLAUDE.md` is the map (where things live, the commands, the conventions, the agent workflow),
`docs/PLAN.md` the design, `docs/reference/` how each built area works, and
`docs/project_overview.md` the status, the roadmap and the decisions.

## Licence

The code is under the MIT License (`LICENSE`). Map data is © OpenStreetMap contributors, under
the ODbL; the basemap, the fonts and the other third-party notices are on the app's notices page
(`frontend/public/notices.html`).
