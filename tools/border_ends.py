"""Count the dead ends at a region's edges: where its extracts cut roads off, and whether a
merge of more states joins them up.

    uv run python tools/border_ends.py --region oregon [--compare oregon-washington]
        [--states PATH] [--out tools/out/border_ends-<region>.json]

A dead end is a node that ends a way of the region's filtered roads (`filtered/<region>
.roads.osm.pbf`) and that no other way, nor the same way again, references: a node of one
neighbour in the raw graph. A Geofabrik extract keeps every way that touches its polygon whole,
so a road crossing the state line ends beyond it, in a dead end, wherever the road continuing it
lies outside the extract (`docs/reference/pipeline.md`, **Regions of several states**). The
tool places each dead end in the state polygon it lies in (the Census 5m states, `--states`
or `<data>/aux/census/cb_<vintage>_us_state_5m.zip`; a point on a line between two states lies
in the region if either is its own) and reports those outside the region's states, by state
("none": outside every state, in the sea, a river or another country).

With `--compare R2`, it looks each of those dead ends up in R2's filtered roads: "continues"
(R2 references the node again, so the road goes on), "ends" (a dead end in R2 too: the road
really ends there, or R2 cuts it off as well), or "absent" (R2 does not hold the node). It
prints its counts, writes the JSON report (every outside dead end with its position, state and,
with `--compare`, status) to `--out` (under `tools/out/`, which git ignores) and exits 0. It
reads only files already on disk, so it never downloads; the gate does not run it, since it
needs real extracts.
"""

import argparse
import json
import sys
from array import array
from collections import Counter
from dataclasses import asdict, dataclass
from itertools import groupby
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import osmium
import shapely
from numpy.typing import NDArray

from worst_route.config import REGIONS, STATES
from worst_route.pipeline import layout
from worst_route.presentation.places import census_dir, state_files


@dataclass(frozen=True)
class End:
    """A dead end outside the region's states: its node, position and the state it lies in."""

    node: int
    lon: float
    lat: float
    usps: str  # the state's STUSPS, or "none"


def _ways(roads: Path) -> tuple[NDArray[np.int64], NDArray[np.int64]]:
    """Every node reference of the roads' ways (a node repeated back to back counted once, as
    `build` reads it), and each way's first and last node."""
    refs, ends = array("q"), array("q")
    for way in osmium.FileProcessor(roads, osmium.osm.WAY):
        nodes = [ref for ref, _ in groupby(n.ref for n in way.nodes)]
        refs.extend(nodes)
        ends.extend((nodes[0], nodes[-1]))
    return np.frombuffer(refs, dtype=np.int64), np.frombuffer(ends, dtype=np.int64)


def dead_ends(roads: Path) -> NDArray[np.int64]:
    """The nodes that end a way and are referenced only there, ascending."""
    refs, ends = _ways(roads)
    ids, counts = np.unique(refs, return_counts=True)
    ends = np.unique(ends)
    dead: NDArray[np.int64] = ends[counts[np.searchsorted(ids, ends)] == 1]
    return dead


def locations(roads: Path, nodes: NDArray[np.int64]) -> NDArray[np.float64]:
    """The (lon, lat) of each of `nodes`, in their order."""
    tracker = osmium.IdTracker()
    for node in nodes.tolist():
        tracker.add_node(node)
    found = {
        n.id: (n.location.lon, n.location.lat)
        for n in osmium.FileProcessor(roads, osmium.osm.NODE).with_filter(tracker.id_filter())
    }
    return np.array([found[node] for node in nodes.tolist()], dtype=np.float64).reshape(-1, 2)


def outside_ends(roads: Path, states: Any, home: set[str]) -> list[End]:
    """The dead ends of `roads` outside the states whose STATEFP is in `home`, by node id.

    `states` is a frame with STATEFP, STUSPS and geometry in lon/lat."""
    nodes = dead_ends(roads)
    where = locations(roads, nodes)
    statefp = states["STATEFP"].astype(str).to_numpy()
    stusps = states["STUSPS"].astype(str).to_numpy()
    tree = shapely.STRtree(np.asarray(states.geometry.values))
    hit_point, hit_state = tree.query(shapely.points(where), predicate="intersects")
    in_home = np.zeros(len(nodes), dtype=np.bool_)
    in_home[hit_point[np.isin(statefp[hit_state], sorted(home))]] = True
    usps = ["none"] * len(nodes)
    for p, s in sorted(
        zip(hit_point.tolist(), hit_state.tolist(), strict=True), key=lambda h: -h[1]
    ):
        usps[p] = str(stusps[s])  # the lowest-index state wins a shared line
    return [
        End(int(nodes[k]), float(where[k, 0]), float(where[k, 1]), usps[k])
        for k in np.flatnonzero(~in_home).tolist()
    ]


def statuses(roads: Path, nodes: list[int]) -> dict[int, str]:
    """Whether the road at each of `nodes` "continues", "ends" or is "absent" in `roads`."""
    refs, ends = _ways(roads)
    asked = np.asarray(nodes, dtype=np.int64)
    counts = Counter(refs[np.isin(refs, asked)].tolist())
    ending = set(ends[np.isin(ends, asked)].tolist())
    status = {}
    for node in nodes:
        if counts[node] == 0:
            status[node] = "absent"
        elif counts[node] == 1 and node in ending:
            status[node] = "ends"
        else:
            status[node] = "continues"
    return status


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    states_path = args.states or state_files(census_dir())[0]
    for path in (layout.roads_pbf(args.region), states_path):
        if not path.exists():
            print(f"missing input {path}", file=sys.stderr)
            return 1
    source = f"zip://{states_path}" if states_path.suffix == ".zip" else str(states_path)
    states = gpd.read_file(source, columns=["STATEFP", "STUSPS"]).sort_values(
        "STATEFP", ignore_index=True
    )
    home = {STATES[state][1] for state in REGIONS[args.region].states}
    roads = layout.roads_pbf(args.region)
    total = len(dead_ends(roads))
    ends = outside_ends(roads, states, home)
    by_state = Counter(end.usps for end in ends)
    listed = ", ".join(f"{usps} {n}" for usps, n in by_state.most_common())
    print(f"{args.region}: {total} dead ends; {len(ends)} outside its states ({listed or 'none'})")
    report: dict[str, Any] = {
        "region": args.region,
        "dead_ends": total,
        "outside": {"count": len(ends), "by_state": dict(by_state.most_common())},
    }
    rows = [asdict(end) for end in ends]
    if args.compare is not None:
        other = layout.roads_pbf(args.compare)
        if not other.exists():
            print(f"missing input {other}", file=sys.stderr)
            return 1
        status = statuses(other, [end.node for end in ends])
        for row in rows:
            row["status"] = status[row["node"]]
        tally = Counter(status.values())
        print(
            f"in {args.compare}: {tally['continues']} continue, {tally['ends']} still end, "
            f"{tally['absent']} absent"
        )
        for usps in by_state:
            here = Counter(status[e.node] for e in ends if e.usps == usps)
            print(
                f"  {usps}: {here['continues']} continue, {here['ends']} still end, "
                f"{here['absent']} absent"
            )
        report["compare"] = {"region": args.compare, **{k: tally[k] for k in sorted(tally)}}
    report["ends"] = rows
    out = args.out or Path("tools/out") / f"border_ends-{args.region}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1) + "\n")
    print(f"wrote {out}")
    return 0


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--region", required=True, choices=sorted(REGIONS))
    parser.add_argument("--compare", choices=sorted(REGIONS), default=None)
    parser.add_argument("--states", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=None)
    return parser.parse_args(argv)


if __name__ == "__main__":
    sys.exit(main())
