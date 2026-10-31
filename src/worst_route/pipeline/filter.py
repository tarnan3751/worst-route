"""`pipeline filter`: keep the drivable roads, clipped to a bbox, and collect the POIs.

`docs/PLAN.md` §2.2. `filter_roads` reads each source extract with pyosmium in two passes:

- **Roads.** Highway ways that `exclusions.kept_class` accepts, of a class under the region's
  ceiling when it has one (`config.Region.classes`), and, when a bbox is given, that have
  at least one node location inside it; a way that touches the box is kept whole, so the clip
  leaves no half-ways. Node locations are read only when there is a bbox to clip to. Kept ways
  go through a `BackReferenceWriter`, which adds the nodes they reference, their tags stripped.
- **POIs.** Nodes and ways tagged as in `POI_TAGS`, or with `brand` or `brand:wikidata`, inside
  the bbox, and nodes on a kept way wherever they lie (a level crossing on a road that straddles
  the box): one row per object and kind (`POI_SCHEMA`), at the node, or at the mean of a way's
  node locations (a closed way's repeated first node counted once).

`source_timestamp` reads an extract's data date from its header, for the step's sidecar.

Sources (several for a multi-state build) are read in order; a way or POI already taken from an
earlier source is skipped (the ways taken are kept in an `osmium.index.IdSet`, a bit set, not a
Python set), though the way's nodes are still taken from each source that holds it, and the
per-source road files are merged into one, sorted, each node once at its newest version: so
when the extracts differ only in their shared nodes, the merge writes the roads one extract of
their union would (a way or POI that differs keeps the first extract's version). The merge
(`merge_parts`) streams: it reads the sorted part files in step, one object of each at a time,
so its memory grows with the number of sources, not with their nodes, and it writes what
`osmium.MergeInputReader`'s simplifying merge of them would, byte for byte, a negative id never
reaching it (the `IdSet` and `BackReferenceWriter` refuse one). Outputs are written
to temporary files and renamed into place. Values are copied out of pyosmium's objects, which
are views into its buffers, but for the merge, which writes each object before its file reads
on.
"""

import heapq
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast

import osmium
import pyarrow as pa
import pyarrow.parquet as pq

from worst_route.pipeline.exclusions import RoadClass, Tags, kept_class

type BBox = tuple[float, float, float, float]  # (min_lon, min_lat, max_lon, max_lat)

POI_TAGS: dict[tuple[str, str], str] = {
    ("barrier", "toll_booth"): "toll_booth",
    ("railway", "level_crossing"): "level_crossing",
    ("highway", "stop"): "stop",
    ("highway", "traffic_signals"): "traffic_signals",
}
BRAND_KEYS = ("brand", "brand:wikidata")

POI_SCHEMA = pa.schema(
    [
        ("osm_type", pa.string()),  # "n" or "w"
        ("osm_id", pa.int64()),
        ("lon", pa.float64()),
        ("lat", pa.float64()),
        ("kind", pa.string()),  # a POI_TAGS value, or "brand"
        ("brand", pa.string()),
        ("brand_wikidata", pa.string()),
        ("name", pa.string()),
    ]
)


@dataclass
class FilterStats:
    """What the filter read and kept, across all sources."""

    ways_read: int = 0  # highway ways
    ways_excluded: int = 0  # dropped by the exclusion rules
    ways_above_ceiling: int = 0  # kept by them, but of a class above the region's ceiling
    ways_outside: int = 0  # no node inside the bbox
    ways_duplicate: int = 0  # already taken from an earlier source
    ways_kept: int = 0
    pois: dict[str, int] = field(default_factory=dict)  # rows per kind


def poi_kinds(tags: Tags) -> list[str]:
    """The POI kinds an object's tags make it, in `POI_TAGS` order then "brand"; empty if none."""
    kinds = [kind for (key, value), kind in POI_TAGS.items() if tags.get(key) == value]
    if any(tags.get(key) is not None for key in BRAND_KEYS):
        kinds.append("brand")
    return kinds


def filter_roads(
    sources: list[Path],
    roads_out: Path,
    pois_out: Path,
    bbox: BBox | None,
    classes: frozenset[RoadClass] | None = None,
) -> FilterStats:
    """Write the kept roads of `sources` to `roads_out` and their POIs to `pois_out`; with
    `classes`, only the roads of those classes."""
    stats = FilterStats()
    box = osmium.osm.Box(*bbox) if bbox is not None else None
    roads_out.parent.mkdir(parents=True, exist_ok=True)
    pois_out.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=roads_out.parent) as tmp:
        parts = [Path(tmp, f"roads.{i}.osm.pbf") for i in range(len(sources))]
        taken = osmium.index.IdSet()  # way ids
        on_kept_ways: set[int] = set()  # node ids, collected only when clipping
        for source, part in zip(sources, parts, strict=True):
            _write_roads(source, part, box, classes, taken, on_kept_ways, stats)
        if len(parts) == 1:
            parts[0].replace(roads_out)
        else:
            merged = Path(tmp, "roads.osm.pbf")
            merge_parts(parts, merged)
            merged.replace(roads_out)

        table = _collect_pois(sources, box, on_kept_ways, stats)
        pois_tmp = Path(tmp, "pois.parquet")
        pq.write_table(table, pois_tmp)
        pois_tmp.replace(pois_out)
    return stats


def _write_roads(
    source: Path,
    out: Path,
    box: osmium.osm.Box | None,
    classes: frozenset[RoadClass] | None,
    taken: osmium.index.IdSet,
    on_kept_ways: set[int],
    stats: FilterStats,
) -> None:
    entities = osmium.osm.WAY if box is None else osmium.osm.NODE | osmium.osm.WAY
    processor = osmium.FileProcessor(source, entities)
    if box is not None:
        processor.with_locations()
    processor.with_filter(osmium.filter.EntityFilter(osmium.osm.WAY))
    processor.with_filter(osmium.filter.KeyFilter("highway"))
    with osmium.BackReferenceWriter(out, ref_src=source, overwrite=True) as writer:
        for entity in processor:
            way = cast(osmium.osm.Way, entity)  # the entity filter passes ways only
            stats.ways_read += 1
            road_class = kept_class(way.tags)
            if road_class is None:
                stats.ways_excluded += 1
            elif box is not None and not any(box.contains(n.location) for n in way.nodes):
                stats.ways_outside += 1
            elif classes is not None and road_class not in classes:
                stats.ways_above_ceiling += 1
            elif way.id in taken:
                stats.ways_duplicate += 1
                writer.id_tracker.add_references(way)  # its nodes: the newest wins the merge
            else:
                taken.set(way.id)
                if box is not None:
                    on_kept_ways.update(n.ref for n in way.nodes)
                writer.add_way(way)
                stats.ways_kept += 1


TYPE_ORDER = {"n": 0, "w": 1, "r": 2}  # an OSM file's order: nodes, then ways, then relations


def merge_parts(parts: list[Path], out: Path) -> None:
    """Write the sorted union of the sorted OSM files `parts` to `out`, each object once: its
    newest version, of those the newest timestamp, of those the earliest part's, as
    `osmium.MergeInputReader`'s simplifying merge chooses, but reading the parts in step instead
    of holding them all. Its output is that merge's byte for byte for objects of non-negative ids,
    the only ones a part holds (`osmium.index.IdSet` and `BackReferenceWriter` refuse others),
    each at most once in a part, as `BackReferenceWriter` writes them; a further copy of an
    object in one part is passed over (that merge keeps the part's newest, so the two agree when
    a part's copies are the same).

    A heap holds each part's next object, keyed by (type, id, newest version first, part). The
    copies of one object come off it together, and the one chosen is written as it is (a
    `FileProcessor`'s object stays valid until its own file reads on, which is only after it
    has been written); the others are passed over."""
    pool = osmium.io.ThreadPool()
    files = [iter(osmium.FileProcessor(part, thread_pool=pool)) for part in parts]
    current: list[Any] = [None] * len(files)
    heap: list[tuple[int, int, int, int]] = []

    def advance(k: int) -> None:
        current[k] = obj = cast(osmium.osm.Node | osmium.osm.Way | None, next(files[k], None))
        if obj is not None:
            heapq.heappush(heap, (TYPE_ORDER[obj.type_str()], obj.id, -obj.version, k))

    with osmium.SimpleWriter(out) as writer:
        for k in range(len(files)):
            advance(k)
        while heap:
            kind, oid, _, k = heapq.heappop(heap)
            copies = [k]
            while heap and heap[0][0] == kind and heap[0][1] == oid:
                copies.append(heapq.heappop(heap)[3])
            if len(copies) > 1:  # newest version, newest timestamp, earliest part
                copies.sort(
                    key=lambda j: (-current[j].version, -current[j].timestamp.timestamp(), j)
                )
            writer.add(current[copies[0]])
            for j in copies:
                advance(j)
            while heap and heap[0][0] == kind and heap[0][1] == oid:  # a part's further copy
                advance(heapq.heappop(heap)[3])


def _collect_pois(
    sources: list[Path], box: osmium.osm.Box | None, on_kept_ways: set[int], stats: FilterStats
) -> pa.Table:
    keys = sorted({key for key, _ in POI_TAGS} | set(BRAND_KEYS))
    columns: dict[str, list[Any]] = {name: [] for name in POI_SCHEMA.names}
    taken: set[tuple[str, int]] = set()
    kinds_seen: Counter[str] = Counter()
    for source in sources:
        processor = osmium.FileProcessor(source, osmium.osm.NODE | osmium.osm.WAY)
        processor.with_locations().with_filter(osmium.filter.KeyFilter(*keys))
        for entity in processor:
            obj = cast(osmium.osm.Node | osmium.osm.Way, entity)  # the file is read for both
            kinds = poi_kinds(obj.tags)
            key = (obj.type_str(), obj.id)
            if not kinds or key in taken:
                continue
            location = _location(obj)
            if location is None:
                continue
            on_kept_way = key[0] == "n" and obj.id in on_kept_ways
            if box is not None and not box.contains(location) and not on_kept_way:
                continue
            taken.add(key)
            for kind in kinds:
                columns["osm_type"].append(key[0])
                columns["osm_id"].append(key[1])
                columns["lon"].append(location.lon)
                columns["lat"].append(location.lat)
                columns["kind"].append(kind)
                columns["brand"].append(obj.tags.get("brand"))
                columns["brand_wikidata"].append(obj.tags.get("brand:wikidata"))
                columns["name"].append(obj.tags.get("name"))
                kinds_seen[kind] += 1
    stats.pois = dict(sorted(kinds_seen.items()))
    return pa.table(columns, schema=POI_SCHEMA)


def source_timestamp(path: Path) -> str | None:
    """The extract's `osmosis_replication_timestamp` header (its data date), or None."""
    with osmium.io.Reader(path, osmium.osm.NOTHING) as reader:
        return reader.header().get("osmosis_replication_timestamp") or None


def _location(obj: osmium.osm.Node | osmium.osm.Way) -> osmium.osm.Location | None:
    """A node's location, or the mean of a way's valid node locations (None if it has none)."""
    if isinstance(obj, osmium.osm.Node):
        return obj.location
    refs = list(obj.nodes)
    if len(refs) > 1 and refs[0].ref == refs[-1].ref:
        refs.pop()
    points = [(n.lon, n.lat) for n in refs if n.location.valid()]
    if not points:
        return None
    return osmium.osm.Location(
        sum(lon for lon, _ in points) / len(points), sum(lat for _, lat in points) / len(points)
    )
