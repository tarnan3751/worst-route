# Worst Route

The least efficient way to get anywhere in the US. Set a start, an end and some stops; get a
route that never repeats an intersection or a road and is as bad as the time budget allows.

The pipeline (OpenStreetMap state extract to a cleaned, memory-mapped graph), the engine (the
splice loop and the validator that proves every route legal), the API (jobs streamed over
Server-Sent Events) and the MapLibre frontend are built, for Portland and the distance mode. To
run it, build Portland with `uv run worst-route pipeline <step> --region portland` for each step
from `download` to `qa`, start the API with `uv run worst-route serve --region portland`, and
the map with `cd frontend && npm ci && npm run dev`. `CLAUDE.md` is the map (where things live,
commands, conventions, the agent workflow), `docs/PLAN.md` the design, `docs/reference/` how
each built area works, and `docs/project_overview.md` the status, roadmap and decisions.
