"""The routing graph: one build's arrays, their on-disk format, the backends, spatial helpers.

`data` holds `GraphData` (every array of one build) and its validation; `store` writes and
memory-maps the on-disk build and computes `graph_version`; `backend` is the `GraphBackend`
protocol the engine programs against; `python_backend` is its pure-Python reference
implementation; `spatial` projects and snaps stops and builds the leash and bubble masks;
`testing` builds small graphs by hand for tests. How they work: `docs/reference/graph.md`.
"""
