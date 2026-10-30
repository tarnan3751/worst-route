"""Normalised road refs and names, so two spellings of one road compare equal.

Table-driven, pure functions (`docs/reference/pipeline.md`, cleanup). `normalize_ref` splits
a `ref` tag on `;` and normalises each part: uppercase, runs of spaces and hyphens collapsed
to one space, the long prefixes mapped (`Interstate` → `I`, `US Highway`/`US Hwy`/`U.S.` →
`US`, `State Route`/`State Highway` → `SR`; two-letter state prefixes such as `OR` are kept),
a space put between a letter prefix and its number (`I5` → `I 5`), and a trailing `Spur`,
`Business`/`Bus` or `Alt`/`Alternate` split off into its own token right after the ref, so a
business route never equals its mainline. `normalize_name` casefolds, drops periods, expands
directionals (`NE` → `northeast`) and, except as the first word ("St Johns"), the USPS street
suffixes (`St` → `street`); `name_without_directions` also leaves the directional words out.
`road_signature` is a road's identity for the cleanup steps: its
normalised ref set when it has a ref, else its normalised name, else None. Enrich's identities
reuse these tables later.
"""

import re
from collections.abc import Mapping
from typing import Any

# A road's identity: its normalised ref set, or its normalised name when it has no ref.
type RoadSignature = frozenset[str] | str

_REF_PREFIXES = (
    (re.compile(r"^INTERSTATE(?= |\d|$)"), "I"),
    (
        re.compile(r"^(?:US HIGHWAY|US HWY|US ROUTE|U\.S\. HIGHWAY|U\.S\. ROUTE|U\.S\.)(?= |\d|$)"),
        "US",
    ),
    (re.compile(r"^(?:STATE ROUTE|STATE HIGHWAY|STATE HWY)(?= |\d|$)"), "SR"),
)
_LETTERS_THEN_NUMBER = re.compile(r"^([A-Z]+)(\d)")
_REF_SUFFIXES = {
    "SPUR": "SPUR",
    "BUSINESS": "BUSINESS",
    "BUS": "BUSINESS",
    "ALT": "ALT",
    "ALTERNATE": "ALT",
}

_DIRECTIONALS = {
    "n": "north",
    "s": "south",
    "e": "east",
    "w": "west",
    "ne": "northeast",
    "nw": "northwest",
    "se": "southeast",
    "sw": "southwest",
}
_DIRECTIONS = frozenset(_DIRECTIONALS.values())
_STREET_SUFFIXES = {
    "st": "street",
    "ave": "avenue",
    "rd": "road",
    "blvd": "boulevard",
    "dr": "drive",
    "ln": "lane",
    "ct": "court",
    "pl": "place",
    "hwy": "highway",
    "pkwy": "parkway",
    "ter": "terrace",
    "cir": "circle",
    "wy": "way",
}


def normalize_ref(value: str) -> tuple[str, ...]:
    """The refs in a `ref` tag, normalised, in order, each once."""
    tokens: list[str] = []
    for part in value.split(";"):
        ref = re.sub(r"[\s-]+", " ", part.strip().upper()).strip()
        for pattern, replacement in _REF_PREFIXES:
            ref = pattern.sub(replacement, ref)
        ref = _LETTERS_THEN_NUMBER.sub(r"\1 \2", ref)
        words = ref.split(" ")
        suffix = _REF_SUFFIXES.get(words[-1]) if len(words) > 1 else None
        if suffix is not None:
            ref = " ".join(words[:-1])
        for token in (ref, suffix):
            if token and token not in tokens:
                tokens.append(token)
    return tuple(tokens)


def normalize_name(value: str) -> str:
    """A street name, casefolded, with directionals and street suffixes spelled out."""
    words = value.casefold().replace(".", "").split()
    return " ".join(
        _DIRECTIONALS.get(word) or (_STREET_SUFFIXES.get(word) if i else None) or word
        for i, word in enumerate(words)
    )


def name_without_directions(value: str) -> str:
    """A normalised name without its directional words: "Pacific Highway East" and "SW Pacific
    Hwy E" both become "pacific highway"."""
    words = normalize_name(value).split()
    return " ".join(word for word in words if word not in _DIRECTIONS)


def road_signature(tags: Mapping[str, Any]) -> RoadSignature | None:
    """The normalised ref set when the road has a ref, else its normalised name, else None."""
    ref = tags.get("ref")
    if ref is not None and (refs := normalize_ref(ref)):
        return frozenset(refs)
    name = tags.get("name")
    if name is not None and (normalized := normalize_name(name)):
        return normalized
    return None
