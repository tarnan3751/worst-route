"""The share image's palettes, one per theme of the frontend (`docs/reference/presentation.md`,
**Share image**).

`PALETTES` maps each theme's id to the seven colours the drawing takes from it. `classic`'s
(`CLASSIC`, the drawing's default) are the image's own from before the themes; every other
theme's are its preset's: the surface its panel, the ink its ink, the secondary text and the
state outlines its muted, the network its line (but transit's and ledger's, whose line is black,
a light grey), the baseline its baseline, the route its route's last colour. The fonts stay the
drawing's own. Nothing here imports matplotlib, so the API checks a theme without it.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class SharePalette:
    """The colours of a share image, as hex strings."""

    surface: str  # the background, the route's casing, the halos
    ink: str  # the headline, the comparison, the start's ring and the end's dot
    secondary: str  # the caption and the footer
    network: str  # the build's roads
    outline: str  # the state outlines
    baseline: str  # the sensible route, dashed
    route: str


CLASSIC = SharePalette(
    surface="#fcfcfb",
    ink="#0b0b0b",
    secondary="#52514e",
    network="#dcdbd3",
    outline="#b4b2a9",
    baseline="#4b5563",
    route="#e8452c",
)


def _themed(panel: str, ink: str, muted: str, line: str, route: str, baseline: str) -> SharePalette:
    """A theme's palette from its preset's colours (`route` its route's last)."""
    return SharePalette(
        surface=panel,
        ink=ink,
        secondary=muted,
        network=line,
        outline=muted,
        baseline=baseline,
        route=route,
    )


PALETTES: dict[str, SharePalette] = {
    "classic": CLASSIC,
    "studio": _themed("#ffffff", "#101418", "#677383", "#e5e8ec", "#ff5a1f", "#7c8796"),
    "night": _themed("#0b1020", "#e6e9ff", "#8b93b8", "#1c2340", "#ff3d9a", "#35e0ff"),
    # transit's and ledger's line is black: their network is a light grey instead
    "transit": _themed("#ffffff", "#000000", "#5a5a5a", "#d4d4d4", "#f26b1d", "#000000"),
    "topo": _themed("#eef0ea", "#1f2a1f", "#5c6b5c", "#d3d8cc", "#e07a1f", "#6c7a6c"),
    "arcade": _themed("#0a1e5e", "#ffffff", "#9fb3ff", "#1d3a9a", "#ffe14d", "#7fe7ff"),
    "dashboard": _themed("#141a22", "#e9eef4", "#8d9aaa", "#2a333f", "#f5a524", "#5c6b7c"),
    "atlas": _themed("#fbfaf6", "#15202b", "#5f6b78", "#d8d6cd", "#1b4f9c", "#6b7480"),
    "sign": _themed("#0f6b4a", "#ffffff", "#bfe3d1", "#0b5339", "#f7c600", "#6c757d"),
    "ledger": _themed("#ffffff", "#111111", "#666666", "#cfcfcb", "#b3261e", "#777777"),
}
