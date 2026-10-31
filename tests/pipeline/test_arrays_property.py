"""The arrays engine against the networkx engine on random street lattices (hypothesis).

Each example is a lattice of streets split into ways at random, each way with a random class,
name, speed limit and one-way rule, drawn either way along its street, its node ids shuffled (so
simplification visits them in no particular order); with spurs to a node a few metres from a
crossing (stage A's clusters, whole or split by the edge test), duplicate ways, closed loops,
a divided road whose carriageways are split at different places (twins), and a few ramps and
roundabout edges for stage B. Both engines must give the same simplified and stage A graphs, the
same twins, the same cleaned graph and the same report.
"""

import tempfile
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tests.pipeline.osm import Ways, write_osm
from tests.pipeline.test_arrays import assert_same_cleanup

CLASSES = ("residential", "residential", "tertiary", "primary", "secondary_link")
NAMES = ("Pine Street", "Oak Street", None)
ONEWAY = (None, None, "yes", "-1", "no")


@st.composite
def tags(draw: st.DrawFn) -> dict[str, str]:
    """A way's tags: a class, perhaps a name, a one-way rule, a speed limit, a roundabout."""
    result = {"highway": draw(st.sampled_from(CLASSES))}
    if (name := draw(st.sampled_from(NAMES))) is not None:
        result["name"] = name
    if (oneway := draw(st.sampled_from(ONEWAY))) is not None:
        result["oneway"] = oneway
    if draw(st.booleans()):
        result["maxspeed"] = draw(st.sampled_from(["25 mph", "35 mph"]))
    if draw(st.integers(0, 15)) == 0:
        result["junction"] = "roundabout"
    return result


@st.composite
def lattices(draw: st.DrawFn) -> tuple[dict[int, tuple[float, float]], Ways]:
    """Streets along a lattice's rows and columns, each with a point or two between crossings,
    split into ways at random points: a way often keeps the tags of the one before it, so their
    shared end is contracted, and sometimes not."""
    width = draw(st.integers(2, 5))
    height = draw(st.integers(2, 4))
    spacing = draw(st.sampled_from([30.0, 60.0, 100.0]))
    between = draw(st.integers(1, 2))
    ids = iter(draw(st.permutations(range(1, 2 * (width * height) * (between + 1) + 60))))
    grid = {(i, j): next(ids) for i in range(width) for j in range(height)}
    nodes = {grid[i, j]: (i * spacing, j * spacing) for i, j in grid}
    lines = []
    for crossings in [
        *([(i, j) for i in range(width)] for j in range(height)),
        *([(i, j) for j in range(height)] for i in range(width)),
    ]:
        line = [grid[crossings[0]]]
        for (i, j), (k, m) in zip(crossings[:-1], crossings[1:], strict=True):
            for step in range(1, between + 1):
                node = next(ids)
                f = step / (between + 1)
                nodes[node] = ((i + f * (k - i)) * spacing, (j + f * (m - j)) * spacing)
                line.append(node)
            line.append(grid[k, m])
        lines.append(line)
    ways: Ways = []
    for line in lines:
        cuts = sorted(draw(st.sets(st.integers(1, len(line) - 2))))
        previous: dict[str, str] | None = None
        for a, b in zip([0, *cuts], [*cuts, len(line) - 1], strict=True):
            refs = line[a : b + 1]
            same = previous is not None and draw(st.integers(0, 3)) > 0
            way_tags = dict(previous) if same and previous is not None else draw(tags())
            way = (len(ways) + 1, refs[::-1] if draw(st.integers(0, 4)) == 0 else refs, way_tags)
            ways.append(way)
            previous = way_tags
            if draw(st.integers(0, 9)) == 0:  # a duplicate way under another id
                ways.append((len(ways) + 1, way[1], dict(way_tags)))
    for _ in range(draw(st.integers(0, 4))):  # a spur to a node a few metres off a crossing
        (i, j) = draw(st.sampled_from(sorted(grid)))
        node, far = next(ids), next(ids)
        dx, dy = draw(st.floats(-20, 20)), draw(st.floats(-20, 20))
        nodes[node] = (i * spacing + dx, j * spacing + dy)
        nodes[far] = (i * spacing + dx + 150.0, j * spacing + dy + 150.0)
        refs = [grid[i, j], node, far] if draw(st.booleans()) else [node, far]
        ways.append((len(ways) + 1, refs, draw(tags())))
    if draw(st.booleans()):  # a divided road south of the lattice, its carriageways split apart
        gap = draw(st.sampled_from([20.0, 30.0, 45.0]))
        road = {"highway": draw(st.sampled_from(["primary", "trunk", "motorway"]))}
        road |= {"ref": "OR 99W", "oneway": "yes"}
        for side, y in ((1, -60.0), (-1, -60.0 - gap)):
            line = [next(ids) for _ in range(6)]
            for k, node in enumerate(line):
                nodes[node] = (k * spacing * 0.7 + draw(st.floats(0, 5)), y)
            line = line if side > 0 else line[::-1]
            cuts = sorted(draw(st.sets(st.integers(1, 4), max_size=3)))
            for a, b in zip([0, *cuts], [*cuts, 5], strict=True):
                ways.append((len(ways) + 1, line[a : b + 1], dict(road)))
    if draw(st.booleans()):  # a closed loop off a lattice node, of one way or of several
        corner = draw(st.sampled_from(sorted(grid)))
        a, b, c = next(ids), next(ids), next(ids)
        x, y = corner[0] * spacing, corner[1] * spacing
        nodes[a], nodes[b], nodes[c] = (x - 80, y - 30), (x - 120, y), (x - 80, y + 30)
        loop = [grid[corner], a, b, c, grid[corner]]
        loop_tags = {"highway": "residential", "name": "Elm Loop"}
        if draw(st.booleans()):
            ways.append((len(ways) + 1, loop, loop_tags))
        else:
            for refs in (loop[:2], loop[1:3], loop[2:4], loop[3:]):
                ways.append((len(ways) + 1, refs, dict(loop_tags)))
    return nodes, ways


@pytest.mark.slow
@settings(max_examples=40, deadline=None)
@given(lattices())
def test_random_lattices_clean_up_the_same(
    lattice: tuple[dict[int, tuple[float, float]], Ways],
) -> None:
    nodes, ways = lattice
    with tempfile.TemporaryDirectory() as tmp:
        assert_same_cleanup(write_osm(Path(tmp) / "lattice.osm", nodes, ways))
