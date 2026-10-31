"""The array engine of the pipeline (`docs/reference/pipeline.md`, **The arrays engine**).

The same steps as the networkx engine, on NumPy arrays, scipy, shapely and Numba instead of
networkx and OSMnx, so that a state and later the whole country fit in memory and take hours,
not days; it is the default engine of `pipeline build` and `pipeline cleanup` (`--engine`). It
makes exactly the build the networkx engine makes: the parity tests
(`tests/pipeline/test_arrays*.py`) compare the two byte for byte. Built so far: `build`
(`build.py`, the stage in `stage.py`) and cleanup: simplification (`simplify.py`), stage A
(`stage_a.py`), twins (`twins.py`), stage B (`stage_b.py`) and connectivity (`connectivity.py`),
whose result `to_nx.py` hands to the networkx steps after it, as networkx itself would hold it
(`order.py`).
"""
