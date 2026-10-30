# Tests that need a built graph carry `@pytest.mark.data` (registered in pyproject.toml). They
# read the build through WORST_ROUTE_DATA, run on demand with `uv run pytest -m data`, and are
# skipped by the gate, which runs `pytest -m "not data"`. Everything else uses the fixtures
# under tests/fixtures/ and never reads data/ or reaches the network.
