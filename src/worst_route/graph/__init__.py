"""The routing graph: one build's arrays, their on-disk format, and test builders.

`data` holds `GraphData` (every array of one build) and its validation; `store` writes and
memory-maps the on-disk build and computes `graph_version`; `testing` builds small graphs by
hand for tests.
"""
