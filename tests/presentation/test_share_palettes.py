"""The share image's palettes: one per theme of the frontend, every colour a hex string, classic's
the image's colours from before the themes."""

import re
from dataclasses import fields

from worst_route.presentation.share_palettes import CLASSIC, PALETTES, SharePalette

THEMES = [
    "classic",
    "studio",
    "night",
    "transit",
    "topo",
    "arcade",
    "dashboard",
    "atlas",
    "sign",
    "ledger",
]


def test_every_theme_has_every_colour_as_hex() -> None:
    assert list(PALETTES) == THEMES
    names = [f.name for f in fields(SharePalette)]
    assert names == ["surface", "ink", "secondary", "network", "outline", "baseline", "route"]
    for theme, palette in PALETTES.items():
        for name in names:
            assert re.fullmatch(r"#[0-9a-f]{6}", getattr(palette, name)), (theme, name)
    assert len(set(PALETTES.values())) == len(THEMES)


def test_classic_is_today_s_image() -> None:
    assert PALETTES["classic"] is CLASSIC
    assert (CLASSIC.surface, CLASSIC.route, CLASSIC.baseline) == ("#fcfcfb", "#e8452c", "#4b5563")


def test_a_theme_takes_its_preset_s_colours() -> None:
    night = PALETTES["night"]  # panel, ink, muted, line; muted again, baseline, the route's last
    assert night == SharePalette(
        "#0b1020", "#e6e9ff", "#8b93b8", "#1c2340", "#8b93b8", "#35e0ff", "#ff3d9a"
    )
    # transit's and ledger's line is black: their network a light grey
    assert (PALETTES["transit"].network, PALETTES["ledger"].network) == ("#d4d4d4", "#cfcfcb")
