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
`road_letter` is the letter a name starts with for the alphabet route (`engine/alphabet_search.py`):
its first letter once the directional words that lead it are dropped (but a directional followed
by a street type alone, "North Street", is the name), or None (a numbered street, or no Latin
letter), which makes the road a free connector.
`road_signature` is a road's identity for the cleanup steps: its normalised ref set when it has a
ref, else its normalised name, else None. `identity_of` is the one identity enrich stores per
edge (hard mode, directions): the highest-class ref, ranked Interstate, US, state (a state's
postal code or `SR`), then any other, alphabetically within a class, each ref keeping its suffix
(`US 26 BUSINESS` is not `US 26`); else the normalised name.
"""

import re
import unicodedata
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

# Ref prefixes ranked as state routes by `identity_of`: the states' and DC's postal codes, and
# SR (`State Route`). Others, such as CR (county roads), rank below.
_STATE_CODES = (
    "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH "
    "NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY SR"
)
_STATE_PREFIXES = frozenset(_STATE_CODES.split())

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
_STREET_TYPES = frozenset(_STREET_SUFFIXES.values())


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


def road_letter(value: str) -> str | None:
    """The letter, A to Z, a road named `value` starts with for the alphabet route, or None.

    The name is normalised (`normalize_name`: casefolded, `NW` → `northwest`), and its leading
    directional words are dropped unless only a street type follows one, so "Southwest Broadway"
    is B, "NW Glisan St" G and "Southwest Parkway Avenue" P, but "North Street" is N and
    "E Avenue" E.
    Then, accents taken off (NFKD), its first letter or digit decides: a letter a-z is the
    road's letter, uppercased; a digit (a numbered street, "Northeast 82nd Avenue") or any other
    letter (another script), or no letter or digit at all, is None, a free connector.
    """
    words = normalize_name(value).split()
    while len(words) > 1 and words[0] in _DIRECTIONS:
        if len(words) == 2 and words[1] in _STREET_TYPES:  # "North Street": the name itself
            break
        words = words[1:]
    for char in unicodedata.normalize("NFKD", " ".join(words)):
        if "a" <= char <= "z":
            return char.upper()
        if char.isalnum():  # a digit, or a letter of another script
            return None
    return None


def road_signature(tags: Mapping[str, Any]) -> RoadSignature | None:
    """The normalised ref set when the road has a ref, else its normalised name, else None."""
    ref = tags.get("ref")
    if ref is not None and (refs := normalize_ref(ref)):
        return frozenset(refs)
    name = tags.get("name")
    if name is not None and (normalized := normalize_name(name)):
        return normalized
    return None


def identity_of(tags: Mapping[str, Any]) -> str | None:
    """The road's identity: its highest-class ref, suffix kept, else its normalised name."""
    ref = tags.get("ref")
    if ref is not None:
        refs = [joined for part in ref.split(";") if (joined := " ".join(normalize_ref(part)))]
        if refs:
            return min(refs, key=lambda r: (_ref_rank(r), r))
    name = tags.get("name")
    if name is not None and (normalized := normalize_name(name)):
        return normalized
    return None


def _ref_rank(ref: str) -> int:
    """0 Interstate, 1 US, 2 state, 3 any other ref."""
    words = ref.split(" ")
    if len(words) < 2 or not words[1][:1].isdigit():
        return 3
    if words[0] == "I":
        return 0
    if words[0] == "US":
        return 1
    return 2 if words[0] in _STATE_PREFIXES else 3
