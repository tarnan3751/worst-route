"""The offline pipeline: download, filter, build, then cleanup through qa (`docs/PLAN.md` §2).

One module per step, each run by `worst-route pipeline <step>`; how the built steps work, the
data layout, build ids and sidecars are `docs/reference/pipeline.md`. `PIPELINE_VERSION` enters
every build id: bump it when a step's output changes for the same input.
"""

PIPELINE_VERSION = "2"  # 2: bearings over the road, less cleanup's stubs
