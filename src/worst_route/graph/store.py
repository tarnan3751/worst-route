"""The on-disk build format and `graph_version` (`docs/reference/graph.md`, **The build format**).

One directory per build, `data/graphs/<region>/<build_id>/`:

    meta.json           GraphData.meta, with graph_version filled in
    strings.json        {"names": [...], "refs": [...], "identities": [...], "brands": [...],
                         "brand_wikidata": [...]}
    arrays/<name>.npy   one file per ARRAY_SPECS entry, written by np.save without pickles

`write_build(data, path)` validates the build and writes it into the build directory `path`,
which may already exist and hold other files (the pipeline's `stages/` and sidecars). It
refuses a `path` that has `meta.json`: a written build is never overwritten (builds are
immutable per build id). It stages everything in a sibling temporary directory, moves
`strings.json` and `arrays/` into `path`, and moves `meta.json` in last, as the commit marker:
`read_build` needs it, so no reader ever sees half a build. `arrays/` or `strings.json` without
`meta.json` are what a write that crashed between its moves left behind, and are replaced; a
staging directory a crashed write left is cleared.

`read_build(path, mmap=True)` memory-maps every array read-only (`np.load(mmap_mode="r")`), so
worker processes share the OS page cache; `mmap=False` loads read-only copies. Reading checks
dtypes, shapes and meta only (`validate(deep=False)`): a deep validation or a re-hash would
read every page of the build in every worker. A build without one of the arrays raises
`MissingArray`, one line naming the remedy: when another pipeline version wrote the build
(`outdated`, its `meta.pipeline_version` against `PIPELINE_VERSION`), the rebuild of the region
from `pipeline filter` on, which gives it a new build id; otherwise the missing array and the
`pipeline save --force` that writes the build again.

`graph_version(data)` is the sha256 over, in ARRAY_SPECS order, each array's name, dtype,
shape and raw bytes, then the strings.json bytes, then the canonical JSON of meta["params"]
(sorted keys; `validate()` requires params to be JSON-native with string keys, so that JSON
stands for the params exactly). Nothing else in meta enters it, so rewriting the same build
reproduces it.
"""

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import numpy as np

from worst_route.config import PIPELINE_VERSION
from worst_route.graph.data import ARRAY_SPECS, GraphData, Strings


def graph_version(data: GraphData) -> str:
    """The sha256 of the build's arrays, strings and params, as a hex string."""
    digest = hashlib.sha256()
    for name in ARRAY_SPECS:
        a = np.ascontiguousarray(getattr(data, name))
        digest.update(f"{name}|{a.dtype.str}|{list(a.shape)}\n".encode())
        digest.update(a)
    digest.update(_strings_json(data.strings))
    digest.update(json.dumps(data.meta["params"], sort_keys=True, separators=(",", ":")).encode())
    return digest.hexdigest()


def write_build(data: GraphData, path: Path) -> str:
    """Validate `data`, write it into the build directory `path`, return its graph_version."""
    data.validate()
    if (path / "meta.json").exists():
        raise FileExistsError(f"{path / 'meta.json'}: a build is already written there")
    version = graph_version(data)
    tmp = path.with_name(f".{path.name}.tmp")
    if tmp.exists():  # left by a write that crashed while staging
        shutil.rmtree(tmp)
    (tmp / "arrays").mkdir(parents=True)
    for name in ARRAY_SPECS:
        np.save(tmp / "arrays" / f"{name}.npy", getattr(data, name), allow_pickle=False)
    (tmp / "strings.json").write_bytes(_strings_json(data.strings))
    meta = {**data.meta, "graph_version": version}
    (tmp / "meta.json").write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n")
    path.mkdir(parents=True, exist_ok=True)
    if (path / "arrays").exists():  # left by a write that crashed between its moves
        shutil.rmtree(path / "arrays")
    (tmp / "strings.json").replace(path / "strings.json")
    (tmp / "arrays").rename(path / "arrays")
    (tmp / "meta.json").replace(path / "meta.json")  # last: the commit marker
    tmp.rmdir()
    return version


class MissingArray(FileNotFoundError):  # noqa: N818 (reads as the condition)
    """A build without one of the format's arrays (one saved before the array existed)."""


def outdated(path: Path, meta: dict[str, Any]) -> str | None:
    """For a build another pipeline version wrote, the line naming the rebuild; else None."""
    if meta.get("pipeline_version") == PIPELINE_VERSION:
        return None
    return (
        f"the build at {path} was written by pipeline {meta.get('pipeline_version')} "
        f"(now {PIPELINE_VERSION}): rebuild the region with `worst-route pipeline filter "
        f"--region {meta['region']}` and the steps after it"
    )


def read_build(path: Path, mmap: bool = True) -> GraphData:
    """Load the build at `path`, every array memory-mapped read-only (`mmap=False`: copies)."""
    meta = json.loads((path / "meta.json").read_text())
    strings = json.loads((path / "strings.json").read_text(encoding="utf-8"))
    arrays: dict[str, Any] = {}
    for name in ARRAY_SPECS:
        file = path / "arrays" / f"{name}.npy"
        if not file.exists():
            raise MissingArray(
                outdated(path, meta)
                or f"the build at {path} lacks the array {name}: rebuild it with "
                f"`worst-route pipeline save --force --region {meta['region']}`"
            )
        a = np.load(file, mmap_mode="r" if mmap else None, allow_pickle=False)
        a.flags.writeable = False
        arrays[name] = a
    data = GraphData(
        **arrays,
        strings=Strings(
            names=strings["names"],
            refs=strings["refs"],
            identities=strings["identities"],
            brands=strings["brands"],
            brand_wikidata=strings["brand_wikidata"],
        ),
        meta=meta,
    )
    data.validate(deep=False)
    return data


def _strings_json(strings: Strings) -> bytes:
    tables = {
        "names": strings.names,
        "refs": strings.refs,
        "identities": strings.identities,
        "brands": strings.brands,
        "brand_wikidata": strings.brand_wikidata,
    }
    return json.dumps(tables, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
