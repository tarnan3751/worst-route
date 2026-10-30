"""The query as the proxy validates, sends and caches it."""

from hypothesis import given
from hypothesis import strategies as st

from worst_route.geocode.base import normalise


def test_normalise_casefolds_collapses_whitespace_and_strips() -> None:
    assert normalise("  Powell's\tCITY  of\n Books ") == "powell's city of books"
    assert normalise("Straße Nord") == "strasse nord"  # casefold, and NBSP is whitespace
    assert normalise(" \t\n ") == ""


@given(st.text())
def test_normalise_is_idempotent(query: str) -> None:
    once = normalise(query)
    assert normalise(once) == once
    assert once == once.strip() and "  " not in once
