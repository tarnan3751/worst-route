"""Random road graphs for the parity tests, as a reviewer drew them: a jittered grid of junctions
(some coincident) walked by ways of every shape the pipeline must read, in metres for
`osm.write_osm`. `random_roads(seed)` is the same graph for the same seed.

The ways: random walks split into ways, some reversed, with a node repeated back to back, or
duplicated under another id or the same one, or with a parallel way of their own shape points;
dead-end spurs; rings closed by one way or several, lollipops; a ring touching nothing; a node
shared by three ways; one-node ways, footways (not kept), an id first a footway then a road.
"""

from typing import Any

import numpy as np

from tests.pipeline.osm import Ways

CLASSES = ["residential", "residential", "tertiary", "primary", "secondary", "motorway"]
CLASSES += ["motorway_link", "trunk", "unclassified", "living_street", "primary_link"]


def random_tags(rng: np.random.Generator) -> dict[str, str]:
    """A way's tags: a class, and maybe a name, a one-way rule, a roundabout, a speed, a bridge,
    a ref."""
    t = {"highway": str(rng.choice(CLASSES))}
    if rng.random() < 0.05:
        t = {"highway": "service", "service": "alley"}
    if rng.random() < 0.6:
        t["name"] = str(rng.choice(["Pine", "Oak", "Elm"]))
    r = rng.random()
    if r < 0.15:
        t["oneway"] = "yes"
    elif r < 0.22:
        t["oneway"] = "-1"
    elif r < 0.27:
        t["oneway"] = "no"
    elif r < 0.30:
        t["oneway"] = "reversible"
    if rng.random() < 0.08:
        t["junction"] = "roundabout"
    if rng.random() < 0.3:
        t["maxspeed"] = str(rng.choice(["25 mph", "35 mph"]))
    if rng.random() < 0.05:
        t["bridge"] = "yes"
        t["layer"] = "1"
    if rng.random() < 0.05:
        t["ref"] = "OR 99"
    return t


def random_roads(seed: int) -> tuple[dict[int, tuple[float, float]], Ways]:
    """The nodes (id to metres east and north) and ways of the graph of `seed`."""
    rng = np.random.default_rng(seed)
    w, h = int(rng.integers(3, 7)), int(rng.integers(3, 6))
    spacing = float(rng.choice([12.0, 40.0, 90.0]))
    it = iter(rng.permutation(np.arange(1, 4000)).tolist())
    grid: dict[tuple[int, int], int] = {}
    nodes: dict[int, tuple[float, float]] = {}
    for i in range(w):
        for j in range(h):
            n = next(it)
            grid[i, j] = n
            nodes[n] = (i * spacing + rng.normal(0, 2), j * spacing + rng.normal(0, 2))

    def between(a: int, b: int, k: int) -> list[int]:
        pa, pb = nodes[a], nodes[b]
        out = []
        for s in range(1, k + 1):
            n = next(it)
            f = s / (k + 1)
            nodes[n] = (pa[0] + f * (pb[0] - pa[0]), pa[1] + f * (pb[1] - pa[1]))
            out.append(n)
        return out

    shape_points: dict[tuple[int, int], list[int]] = {}

    def mid(a: int, b: int) -> list[int]:
        key = (min(a, b), max(a, b))
        if key not in shape_points:
            shape_points[key] = between(key[0], key[1], int(rng.integers(0, 3)))
        points = shape_points[key]
        return points if a < b else points[::-1]

    ways: Ways = []
    way_ids = iter(range(1, 100000))

    def add(refs: list[int], tags: dict[str, Any]) -> None:
        ways.append((next(way_ids), refs, tags))

    for _ in range(int(rng.integers(4, 12))):  # random walks
        i, j = int(rng.integers(0, w)), int(rng.integers(0, h))
        walk = [grid[i, j]]
        visited = {(i, j)}
        for _ in range(int(rng.integers(1, 8))):
            moves = [(i + 1, j), (i - 1, j), (i, j + 1), (i, j - 1)]
            moves = [m for m in moves if m in grid and (m not in visited or rng.random() < 0.15)]
            if not moves:
                break
            ni, nj = moves[int(rng.integers(0, len(moves)))]
            walk += mid(grid[i, j], grid[ni, nj]) + [grid[ni, nj]]
            i, j = ni, nj
            visited.add((i, j))
        if len(walk) < 2:
            continue
        cuts = []  # split into ways
        if len(walk) > 2:
            cuts = sorted(
                set(rng.integers(1, len(walk) - 1, size=int(rng.integers(0, 3))).tolist())
            )
        tags = random_tags(rng)
        for a, b in zip([0, *cuts], [*cuts, len(walk) - 1], strict=True):
            refs = walk[a : b + 1]
            if rng.random() < 0.5:
                tags = random_tags(rng) if rng.random() < 0.4 else dict(tags)
            if rng.random() < 0.2:
                refs = refs[::-1]
            if rng.random() < 0.1 and len(refs) > 1:  # a node repeated back to back
                k = int(rng.integers(0, len(refs)))
                refs = refs[: k + 1] + [refs[k]] + refs[k + 1 :]
            add(refs, dict(tags))
            r = rng.random()
            if r < 0.08:  # duplicated under another id
                add(list(refs), dict(tags))
            elif r < 0.14:  # duplicated under the same id (read once)
                ways.append((ways[-1][0], list(refs[::-1]), random_tags(rng)))
            elif r < 0.18:  # a parallel way, with shape points of its own
                a0, b0 = refs[0], refs[-1]
                if a0 != b0:
                    p = next(it)
                    nodes[p] = (nodes[a0][0] + 5.0, nodes[a0][1] + 7.0)
                    add([a0, p, b0], dict(tags))
    for _ in range(int(rng.integers(0, 4))):  # dead-end spurs
        g = grid[int(rng.integers(0, w)), int(rng.integers(0, h))]
        p, q = next(it), next(it)
        nodes[p] = (nodes[g][0] + 20.0, nodes[g][1] - 15.0)
        nodes[q] = (nodes[g][0] + 40.0, nodes[g][1] - 30.0)
        add([g, p, q] if rng.random() < 0.5 else [q, p, g], random_tags(rng))
    for _ in range(int(rng.integers(0, 3))):  # rings: one way, several ways, lollipops
        g = grid[int(rng.integers(0, w)), int(rng.integers(0, h))]
        a, b, c = next(it), next(it), next(it)
        x, y = nodes[g]
        nodes[a], nodes[b], nodes[c] = (x - 30, y - 10), (x - 45, y), (x - 30, y + 10)
        kind = rng.random()
        t = random_tags(rng)
        if kind < 0.33:
            add([g, a, b, c, g], t)
        elif kind < 0.66:
            for refs in ([g, a], [a, b], [b, c], [c, g]):
                add(refs, dict(t))
        else:  # a stick, then a loop back to the stick's end
            s = next(it)
            nodes[s] = (x - 15, y)
            add([g, s, a, b, c, s], t)
    if rng.random() < 0.3:  # a ring touching nothing
        r1, r2, r3 = next(it), next(it), next(it)
        nodes[r1], nodes[r2], nodes[r3] = (-200, -200), (-150, -200), (-175, -160)
        t = random_tags(rng)
        add([r1, r2], dict(t))
        add([r2, r3], dict(t))
        add([r3, r1], dict(t))
    g = grid[int(rng.integers(0, w)), int(rng.integers(0, h))]  # a node shared by three ways
    for d in range(3):
        p = next(it)
        nodes[p] = (nodes[g][0] + 25 * np.cos(d * 2.1), nodes[g][1] + 25 * np.sin(d * 2.1))
        add([p, g] if d % 2 else [g, p], random_tags(rng))
    add([g, g], random_tags(rng))  # a one-node way, footways, an id first a footway
    add([grid[0, 0], grid[1, 0]], {"highway": "footway"})
    if rng.random() < 0.5:
        k = next(way_ids)
        ways.append((k, [grid[0, 1], grid[1, 1]], {"highway": "footway"}))
        ways.append((k, [grid[0, 1], grid[1, 1]], random_tags(rng)))
    if rng.random() < 0.3:  # a coincident node
        c1 = next(it)
        nodes[c1] = nodes[grid[1, 1]]
        add([grid[1, 1] if rng.random() < 0.5 else grid[0, 0], c1, grid[1, 2]], random_tags(rng))
    return nodes, ways
