"""The array engine of the pipeline (`docs/reference/pipeline.md`, **The arrays engine**).

The same steps as the networkx engine, on NumPy arrays, scipy, shapely and Numba instead of
networkx and OSMnx, so that a state and later the whole country fit in memory and take hours,
not days; it is the default engine of `pipeline build` and `pipeline cleanup` (`--engine`), and
`enrich`, `tiers` and `save` follow the engine cleanup ran. It makes exactly the build the
networkx engine makes: the parity tests (`tests/pipeline/test_arrays*.py`) compare the two byte
for byte. The steps: `build` (`build.py`, its stages in `stage.py`); cleanup's simplification
(`simplify.py`), stage A (`stage_a.py`), twins (`twins.py`), stage B (`stage_b.py`) and
connectivity (`connectivity.py`), all in networkx's order (`order.py`), the cleaned graph written
as a stage by `cleaned.py`; enrich (`enrich.py`); the tiers (`tiers.py`); and the build, through
`save.graph_data_of`. `to_nx.py` hands its graphs to networkx as networkx itself would hold
them, for the tests' comparisons.
"""
