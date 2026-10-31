"""The share image: a route drawn from our own data, with its ratio and a comparison
(`docs/reference/presentation.md`, **Share image**; `docs/PLAN.md` §7 M4).

`render_share_image` returns a 1200 × 630 PNG (the Open Graph size). The left column holds the
headline, as large as fits, and its caption (`headline`): the ratio of lengths ("42.9×", "less
efficient than the sensible route"), for a slowest-time route the time ratio ("slower than the
sensible route"), for a round trip, which has neither, its length ("299 mi", "to end up where
it started"); and the comparison line at the bottom (wrapped, never between a number and its
unit). The map fills the rest, edge to edge, with no basemap: the build's roads as hairlines
(one edge per road unit, along the build's geometry: those whose bounds meet the map's, in the
build's CRS grown by `NETWORK_MARGIN_M` and then in lon/lat, decoded only where their ends and
lengths cannot tell; only tiers T0-T2 when more than `MAX_NETWORK_EDGES` would show), the state
outlines from the places index when there is one, the route in red on a white casing, the
baseline dashed over it, a ring at the start and a smaller dot at the end (inside the ring, for
a round trip). Its extent is the route's bbox with `MARGIN` on each side (at least `MIN_SPAN_M`
across), widened to the map's shape with x scaled by cos(latitude). The attribution sits at the
bottom right.

It draws on a `Figure` with a `FigureCanvasAgg` of its own (never pyplot, nor any process-wide
backend), with DejaVu Sans as matplotlib ships it, given by its file's path, and saves without
the Software stamp, so the same inputs give the same bytes. Importing matplotlib may still build
its font manager's cache (a scan of the system's fonts) the first time on a machine; the API
imports this module only when it first renders (`api/share_api.py`). matplotlib is not
thread-safe, so renders take one lock in turn. The API caches images under its
`SHARE_IMAGE_VERSION`, which goes up whenever this drawing changes.
"""

import io
import math
import re
import textwrap
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import matplotlib
import numpy as np
import shapely
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.collections import LineCollection
from matplotlib.figure import Figure
from matplotlib.font_manager import FontProperties
from matplotlib.patheffects import withStroke
from matplotlib.text import Text
from numpy.typing import NDArray

from worst_route.engine.route import IntArray
from worst_route.graph.data import GraphData
from worst_route.graph.geometry import Geometry, edge_points, edge_xy, lonlat_to_xy
from worst_route.presentation.comparisons import METRES_PER_MILE
from worst_route.presentation.geojson import line_coordinates
from worst_route.presentation.places import PlaceIndex

WIDTH_PX, HEIGHT_PX, DPI = 1200, 630, 100
COLUMN = 0.38  # the text column's share of the width
PAD_PX = 44.0  # around the text
MARGIN = 0.15  # around the route, of its extent, on each side
MIN_SPAN_M = 1000.0
METRES_PER_DEGREE = 111_320.0  # of latitude
MAX_NETWORK_EDGES = 50_000
NETWORK_MARGIN_M = 50.0  # around the map's extent projected, when finding the roads near it
THIN_TIER = 2  # the highest tier drawn when the network is thinned

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
SECONDARY = "#52514e"
NETWORK = "#dcdbd3"
OUTLINE = "#b4b2a9"
BASELINE = "#4b5563"
ROUTE = "#e8452c"

FOOTER = "worst-route · © OpenStreetMap contributors (ODbL) · Boundaries: U.S. Census Bureau"

_FONTS = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
REGULAR = FontProperties(fname=_FONTS / "DejaVuSans.ttf")
BOLD = FontProperties(fname=_FONTS / "DejaVuSans-Bold.ttf")

_RENDERING = threading.Lock()

Points = NDArray[np.float64]


def render_share_image(
    data: GraphData,
    geometry: Geometry,
    route_nodes: IntArray,
    route_edges: IntArray,
    metrics: Mapping[str, Any],
    places: PlaceIndex | None,
    comparison: str,
    *,
    baseline_edges: IntArray | None = None,
    objective: str = "distance",
) -> bytes:
    """The share image of a route (its nodes and edges on `data`, drawn along `geometry`, its
    document's metrics, the objective it was planned for) as PNG bytes, with `comparison` under
    the headline; `places` gives the state outlines."""
    route = np.asarray(line_coordinates(data, geometry, route_edges))
    with _RENDERING:
        fig = Figure(figsize=(WIDTH_PX / DPI, HEIGHT_PX / DPI), dpi=DPI, facecolor=SURFACE)
        FigureCanvasAgg(fig)  # the figure's canvas from now on: Agg
        ax = fig.add_axes((COLUMN, 0.0, 1.0 - COLUMN, 1.0))
        ax.set_axis_off()
        west, east, south, north = _extent(route, (1.0 - COLUMN) * WIDTH_PX / HEIGHT_PX)
        ax.set_xlim(west, east)
        ax.set_ylim(south, north)

        network = _network(data, geometry, (west, south, east, north))
        ax.add_collection(LineCollection(network, colors=NETWORK, linewidths=0.6, zorder=1))
        if places is not None:
            outlines = _outlines(places, (west, south, east, north))
            ax.add_collection(LineCollection(outlines, colors=OUTLINE, linewidths=1.2, zorder=2))
        ax.plot(*route.T, color=SURFACE, linewidth=4.2, solid_joinstyle="round", zorder=3)
        ax.plot(*route.T, color=ROUTE, linewidth=2.0, solid_joinstyle="round", zorder=4)
        if baseline_edges is not None and len(baseline_edges) > 0:
            base = np.asarray(line_coordinates(data, geometry, baseline_edges))
            ax.plot(
                *base.T,
                color=BASELINE,
                linewidth=2.0,
                dashes=(4, 2.5),
                zorder=5,
                path_effects=[withStroke(linewidth=4.0, foreground=SURFACE)],
            )
        # The start a ring, the end a smaller dot on top: a round trip's shows inside its ring.
        ends = ((route_nodes[0], 10, 2.2, SURFACE, INK), (route_nodes[-1], 6.5, 1.5, INK, SURFACE))
        for node, size, width, face, edge in ends:
            ax.plot(
                data.node_lon[node],
                data.node_lat[node],
                marker="o",
                markersize=size,
                markerfacecolor=face,
                markeredgecolor=edge,
                markeredgewidth=width,
                zorder=6,
            )

        _texts(fig, headline(metrics, objective), comparison)
        out = io.BytesIO()
        fig.savefig(out, format="png", dpi=DPI, facecolor=SURFACE, metadata={"Software": None})
    return out.getvalue()


def _extent(points: Points, aspect: float) -> tuple[float, float, float, float]:
    """(west, east, south, north) around `points` with the margin, at `aspect` (width / height
    on screen), x scaled by cos(latitude)."""
    west, south = points.min(axis=0)
    east, north = points.max(axis=0)
    scale = math.cos(math.radians((south + north) / 2))
    least = MIN_SPAN_M / METRES_PER_DEGREE
    width = max((east - west) * scale, least) * (1 + 2 * MARGIN)
    height = max(north - south, least) * (1 + 2 * MARGIN)
    if width / height < aspect:
        width = height * aspect
    else:
        height = width / aspect
    x, y = (west + east) / 2, (south + north) / 2
    half_x, half_y = width / 2 / scale, height / 2
    return x - half_x, x + half_x, y - half_y, y + half_y


def _network(
    data: GraphData, geometry: Geometry, bbox: tuple[float, float, float, float]
) -> list[Points]:
    """The lines of the road units whose bounds meet `bbox`, thinned to T0-T2 when too many:
    those whose bounds meet the map's in the build's CRS (`_outline_box`), then in lon/lat. A
    unit with an end inside the box meets it, and one whose ends are both beyond its length
    from it misses it: only the others are decoded to tell, then the units drawn."""
    units = np.flatnonzero(data.edge_unit == np.arange(data.m))
    box, at = _outline_box(data, bbox), (data.node_x, data.node_y)
    # Every point of a line lies within its length of either end on each axis (a metre more
    # for the decoding's rounding, half a metre on each axis, and the float32 length).
    reach = data.edge_length_m[units].astype(np.float64) + 1.0
    near = _ends_near(data, units, at, box)
    undecided = np.flatnonzero(~near & _ends_near(data, units, at, box, reach))
    points, counts = edge_xy(data, geometry, units[undecided])
    near[undecided] = _meets(points, counts, box)
    units = units[near]
    inside = _ends_near(data, units, (data.node_lon, data.node_lat), bbox)
    undecided = np.flatnonzero(~inside)
    points, counts = edge_points(data, geometry, units[undecided])
    inside[undecided] = _meets(points, counts, bbox)
    units = units[inside]
    if len(units) > MAX_NETWORK_EDGES:
        units = units[data.edge_tier[units] <= THIN_TIER]
    points, counts = edge_points(data, geometry, units)
    starts = np.cumsum(counts) - counts
    ends = zip(starts.tolist(), (starts + counts).tolist(), strict=True)
    return [points[a:b] for a, b in ends]


def _outline_box(
    data: GraphData, bbox: tuple[float, float, float, float]
) -> tuple[float, float, float, float]:
    """The bounds in the build's CRS of `bbox`'s outline projected (nine points a side), grown
    by `NETWORK_MARGIN_M`, as (x0, y0, x1, y1)."""
    west, south, east, north = bbox
    t = np.linspace(0.0, 1.0, 9)
    lon = np.concatenate([west + (east - west) * t, np.full(9, east), east - (east - west) * t])
    lon = np.concatenate([lon, np.full(9, west)])
    lat = np.concatenate([np.full(9, south), south + (north - south) * t, np.full(9, north)])
    lat = np.concatenate([lat, north - (north - south) * t])
    x, y = lonlat_to_xy(data, lon, lat)
    margin = NETWORK_MARGIN_M
    return (
        float(x.min()) - margin,
        float(y.min()) - margin,
        float(x.max()) + margin,
        float(y.max()) + margin,
    )


def _ends_near(
    data: GraphData,
    edges: NDArray[np.intp],
    at: tuple[Points, Points],
    box: tuple[float, float, float, float],
    reach: Points | float = 0.0,
) -> NDArray[np.bool_]:
    """Which of `edges` have an end inside `box` (x0, y0, x1, y1) grown by `reach` on each side
    (one for all of them, or one each), their nodes at `at` (every node's x and y)."""
    x, y = at
    x0, y0, x1, y1 = box
    near = np.zeros(len(edges), dtype=np.bool_)
    for end in (data.edge_u[edges], data.edge_v[edges]):
        ex, ey = x[end], y[end]
        near |= (ex >= x0 - reach) & (ex <= x1 + reach) & (ey >= y0 - reach) & (ey <= y1 + reach)
    return near


def _meets(
    points: Points, counts: NDArray[np.int64], box: tuple[float, float, float, float]
) -> NDArray[np.bool_]:
    """Which lines, `counts[k]` of `points` each in a row, have bounds meeting `box`
    (x0, y0, x1, y1)."""
    x0, y0, x1, y1 = box
    starts = np.cumsum(counts) - counts
    x, y = points[:, 0], points[:, 1]
    meets: NDArray[np.bool_] = (
        (np.maximum.reduceat(x, starts) >= x0)
        & (np.minimum.reduceat(x, starts) <= x1)
        & (np.maximum.reduceat(y, starts) >= y0)
        & (np.minimum.reduceat(y, starts) <= y1)
    )
    return meets


def _outlines(places: PlaceIndex, bbox: tuple[float, float, float, float]) -> list[Points]:
    """The boundary lines of the state outlines that meet `bbox`."""
    shapes = places.outlines[shapely.intersects(places.outlines, shapely.box(*bbox))]
    return [shapely.get_coordinates(p) for p in shapely.get_parts(shapely.boundary(shapes))]


def headline(metrics: Mapping[str, Any], objective: str) -> tuple[str, str]:
    """The big number and its caption (two lines): the time ratio for a route planned for the
    slowest time (`objective` "time"), the ratio of lengths for any other, a round trip's length
    (it has neither ratio). `metrics` are the route document's (`ratio`, `time_ratio`,
    `length_m`)."""
    ratio = metrics.get("ratio")
    if ratio is None:
        miles = float(metrics["length_m"]) / METRES_PER_MILE
        length = f"{miles:,.1f} mi" if miles < 100 else f"{miles:,.0f} mi"
        return length, "to end up where\nit started"
    if objective == "time":
        return f"{metrics['time_ratio']:,.1f}×", "slower than\nthe sensible route"
    return f"{ratio:,.1f}×", "less efficient than\nthe sensible route"


def _texts(fig: Figure, top: tuple[str, str], comparison: str) -> None:
    """The headline and its caption, the comparison, the footer."""
    room = COLUMN * WIDTH_PX - 2 * PAD_PX
    x = PAD_PX / WIDTH_PX
    big, caption = top
    number = fig.text(
        x, 1 - PAD_PX / HEIGHT_PX, big, fontproperties=BOLD, fontsize=110, color=INK, va="top"
    )
    _fit(number, room)
    below = number.get_window_extent().y0 - 10
    fig.text(
        x,
        below / HEIGHT_PX,
        caption,
        fontproperties=REGULAR,
        fontsize=21,
        color=SECONDARY,
        va="top",
        linespacing=1.3,
    )
    line = fig.text(
        x,
        PAD_PX / HEIGHT_PX,
        textwrap.fill(re.sub(r"(\d) (mi|km)\b", "\\1\u00a0\\2", comparison), width=30),
        fontproperties=REGULAR,
        fontsize=17,
        color=INK,
        va="bottom",
        linespacing=1.35,
    )
    _fit(line, room)
    fig.text(
        1 - 14 / WIDTH_PX,
        12 / HEIGHT_PX,
        FOOTER,
        fontproperties=REGULAR,
        fontsize=9,
        color=SECONDARY,
        ha="right",
        va="bottom",
        path_effects=[withStroke(linewidth=3, foreground=SURFACE)],
    )


def _fit(text: Text, width_px: float) -> None:
    """Shrink `text` until it is at most `width_px` wide."""
    while text.get_window_extent().width > width_px:
        text.set_fontsize(float(text.get_fontsize()) * 0.95)
