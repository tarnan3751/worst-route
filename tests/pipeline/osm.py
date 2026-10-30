"""OSM XML for in-memory test networks, from node positions in metres."""

import math
from pathlib import Path

type Ways = list[tuple[int, list[int], dict[str, str]]]


def write_osm(path: Path, nodes: dict[int, tuple[float, float]], ways: Ways) -> Path:
    """An OSM file from node positions in metres east and north of (-122.6, 45.5)."""
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<osm version="0.6">']
    for node, (x, y) in nodes.items():
        lat = 45.5 + y / 111_132
        lon = -122.6 + x / (111_320 * math.cos(math.radians(45.5)))
        lines.append(f'  <node id="{node}" version="1" lat="{lat:.7f}" lon="{lon:.7f}"/>')
    for way, refs, tags in ways:
        nds = "".join(f'<nd ref="{r}"/>' for r in refs)
        kvs = "".join(f'<tag k="{k}" v="{v}"/>' for k, v in tags.items())
        lines.append(f'  <way id="{way}" version="1">{nds}{kvs}</way>')
    path.write_text("\n".join([*lines, "</osm>"]) + "\n")
    return path
