"""The routing graph: one build's arrays, their on-disk format, the backends, test builders.

`data` holds `GraphData` (every array of one build) and its validation; `store` writes and
memory-maps the on-disk build and computes `graph_version`; `backend` is the `GraphBackend`
protocol the engine programs against; `python_backend` is its pure-Python reference
implementation; `testing` builds small graphs by hand for tests.
"""
