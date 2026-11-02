# Worst Route

Worst Route plans deliberately terrible driving routes. Pick a start, an end and any stops in
between, anywhere in the lower 48, and it finds a way to get there that never uses the same
road or passes the same intersection twice, obeys one-way streets and turn restrictions, and is
as long as it can make it in the time you give it. A 13-minute drive across Portland becomes a
nine-hour tour of the city, with turn-by-turn directions to match.

## What it can do

- Longest route, slowest route, no left turns (or no right turns), collect as many bridges,
  roundabouts, toll booths or level crossings as possible, visit every Starbucks it can find,
  drive roads named A, B, C, … in order, and "hard mode" where each road is driven in one
  stretch and never seen again.
- Prove it. In a small enough area, a solver finds the longest legal route and certifies that
  nothing longer exists.
- Aim for a target: a given multiple of the sensible route, or a given drive time.
- Draw your own route by hand and see if you can beat the engine.
- A route of the day, a set of challenges, a share image, and ten colour themes.

Every route is checked by an independent validator before it is shown, and the same seed on
the same map data always produces the same route.

## Try it

The desktop app runs entirely on your machine and ships with the national road network.
Installers for macOS (Apple Silicon) and Windows, and a tarball for Linux, are attached to each
release. On its first start the app downloads its offline map, about 700 MB;
until then it draws OpenFreeMap's tiles.

To run it from source you need Python 3.12, [uv](https://docs.astral.sh/uv/) and Node 22:

```sh
uv sync
cd frontend && npm ci && cd ..

# Build the Portland road graph from a Geofabrik extract (a few minutes).
for step in download filter build cleanup enrich tiers save qa; do
  uv run worst-route pipeline $step --region portland
done

# Start the API, then the map in another terminal.
uv run worst-route serve --region portland
cd frontend && npm run dev
```

The same pipeline builds any state, or the whole contiguous US at primary roads and above
(`--region us-t3`, about 20 minutes and a lot of RAM). There is also a CLI for planning routes
without the browser: `uv run worst-route plan --help`.

## How it works

The pipeline reads an OpenStreetMap extract, keeps the drivable roads, merges divided highways
and interchanges into single junctions, keeps the turn restrictions, and writes the result as
memory-mapped NumPy arrays. The engine starts from a sensible route and repeatedly splices in
detours, keeping only changes that stay legal and make things worse, until the time budget
runs out. The API streams the route to the browser as it grows. The frontend is plain
TypeScript on MapLibre GL.

Alaska and Hawaii are not covered; neither is connected to the rest of the network by road.

## Documentation

`docs/PLAN.md` is the design, `docs/reference/` describes each part as built, and
`docs/project_overview.md` tracks status and decisions. `CLAUDE.md` is the map of the
repository and its conventions.

## Licence

MIT, see `LICENSE`. Map data © OpenStreetMap contributors, available under the Open Database
License. Notices for the basemap, fonts and other third-party components are on the app's
notices page.
