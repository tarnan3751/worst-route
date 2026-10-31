"""A stage of the arrays engine on disk: one directory, one `.npy` per array.

`write_stage(directory, arrays, tables)` writes each array as `<name>.npy` (no pickles),
`tables.json` (what is not an array: small tables, strings), and `manifest.json` last, which
names every file with its md5: the stage is complete once the manifest exists, and the md5 of
the manifest stands for the whole stage in the next step's sidecar. A manifest left by an
earlier write is removed first, so an interrupted write leaves no stage behind.
`read_stage(directory)` memory-maps the arrays read-only and returns them with the tables.
"""

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from worst_route.pipeline import layout

MANIFEST = layout.STAGE_MANIFEST
TABLES = "tables.json"


def write_stage(
    directory: Path, arrays: Mapping[str, NDArray[Any]], tables: Mapping[str, Any]
) -> None:
    """Write the stage into `directory`, the manifest last."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / MANIFEST).unlink(missing_ok=True)
    files = {}
    for name, array in arrays.items():
        path = directory / f"{name}.npy"
        np.save(path, np.ascontiguousarray(array), allow_pickle=False)
        files[path.name] = layout.file_md5(path)
    (directory / TABLES).write_text(json.dumps(tables, separators=(",", ":")) + "\n")
    files[TABLES] = layout.file_md5(directory / TABLES)
    (directory / MANIFEST).write_text(json.dumps({"files": files}, indent=2) + "\n")


def read_stage(
    directory: Path, *, mmap: bool = True
) -> tuple[dict[str, NDArray[Any]], dict[str, Any]]:
    """The stage's arrays (memory-mapped, read-only, unless `mmap` is False) and tables."""
    files = json.loads((directory / MANIFEST).read_text())["files"]
    arrays = {
        name.removesuffix(".npy"): np.load(
            directory / name, mmap_mode="r" if mmap else None, allow_pickle=False
        )
        for name in files
        if name.endswith(".npy")
    }
    tables: dict[str, Any] = json.loads((directory / TABLES).read_text())
    return arrays, tables
