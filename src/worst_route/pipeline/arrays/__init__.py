"""The array engine of the pipeline (`docs/reference/pipeline.md`, **The arrays engine**).

The same steps as the networkx engine, on NumPy arrays, scipy, shapely and Numba instead of
networkx and OSMnx, so that a state and later the whole country fit in memory and take hours,
not days; `--engine arrays` on `pipeline build` and `pipeline cleanup` selects it. It makes
exactly the build the networkx engine makes: the parity tests (`tests/pipeline/test_arrays*.py`)
compare the two byte for byte. Built so far: `build` (`build.py`, the stage in `stage.py`) and
cleanup's simplification (`simplify.py`), whose result `to_nx.py` hands to the networkx steps
after it, in the order networkx itself would hold it.
"""
