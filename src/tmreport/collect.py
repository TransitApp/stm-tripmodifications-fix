"""Gather everything the map report draws, straight from the repair tool.

Nothing here re-derives a distance or a range: the numbers on the maps are the
ones tmfix computed, so the pictures and the report cannot drift apart. What
this module adds is only what the tool has no reason to keep — stop names and
route names, which matter to a reader and not to the repair.
"""

from __future__ import annotations

import csv
import io
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from google.transit import gtfs_realtime_pb2 as gtfs_rt

from tmfix import download, pipeline
from tmfix.config import PASSWORD_ENV, USERNAME_ENV, Settings
from tmfix.geometry import LatLon, decode_polyline
from tmfix.repair import EntityReport, ModificationReport, RepairResult, repair_feed
from tmfix.static_feed import Pattern, StaticFeed

# The repair tool keeps only stop positions, so names and route labels are read
# here from the same zip it already downloaded.
TripFacts = dict[str, "RouteLabel"]


@dataclass(frozen=True)
class RouteLabel:
    """How a route is named to a reader."""

    short_name: str
    long_name: str
    headsign: str

    def title(self) -> str:
        parts = [part for part in (self.short_name, self.long_name) if part]
        name = " — ".join(parts) if parts else "Unknown route"
        return f"{name} → {self.headsign}" if self.headsign else name


@dataclass
class Panel:
    """What one version of a modification claims about the trip."""

    cancelled_positions: set[int] = field(default_factory=set)
    replacement_stop_ids: list[str] = field(default_factory=list)


@dataclass
class Page:
    """One repaired modification, with everything needed to draw it."""

    entity_id: str
    modification_index: int
    trip_id: str
    route: RouteLabel
    pattern: Pattern
    shape: list[LatLon]
    before: Panel
    after: Panel
    report: ModificationReport
    stop_names: dict[str, str]
    stop_positions: dict[str, LatLon]
    # The trip's scheduled path. Drawing it beside the detour shape is what
    # makes an unserved stop obvious rather than merely asserted.
    scheduled_shape: list[LatLon] = field(default_factory=list)

    @property
    def declared_range(self) -> tuple[int, int] | None:
        return self.report.declared_range

    @property
    def repaired_range(self) -> tuple[int, int] | None:
        return self.report.repaired_range

    @property
    def range_grew(self) -> bool:
        return bool(self.report.added_stops)

    @property
    def stops_dropped(self) -> bool:
        return bool(self.report.dropped_stops)


@dataclass
class Collected:
    """Every page, plus the run's own facts."""

    pages: list[Page]
    feed_timestamp: int
    threshold_m: float
    entities_examined: int
    entities_repaired: int


def _read_table(archive: zipfile.ZipFile, name: str):
    with archive.open(name) as raw:
        yield from csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig", newline=""))


def read_names(zip_path: Path) -> tuple[dict[str, str], TripFacts, dict[str, str]]:
    """Stop names, the route label of every trip, and every trip's shape_id."""
    stop_names: dict[str, str] = {}
    routes: dict[str, tuple[str, str]] = {}
    trip_routes: dict[str, tuple[str, str]] = {}
    trip_shapes: dict[str, str] = {}

    with zipfile.ZipFile(zip_path) as archive:
        for row in _read_table(archive, "stops.txt"):
            stop_names[row["stop_id"]] = (row.get("stop_name") or "").strip()
        for row in _read_table(archive, "routes.txt"):
            routes[row["route_id"]] = (
                (row.get("route_short_name") or "").strip(),
                (row.get("route_long_name") or "").strip(),
            )
        for row in _read_table(archive, "trips.txt"):
            trip_routes[row["trip_id"]] = (
                row.get("route_id", ""),
                (row.get("trip_headsign") or "").strip(),
            )
            if shape_id := (row.get("shape_id") or "").strip():
                trip_shapes[row["trip_id"]] = shape_id

    labels: TripFacts = {}
    for trip_id, (route_id, headsign) in trip_routes.items():
        short_name, long_name = routes.get(route_id, ("", ""))
        labels[trip_id] = RouteLabel(short_name, long_name, headsign)
    return stop_names, labels, trip_shapes


def read_shapes(zip_path: Path, wanted: set[str]) -> dict[str, list[LatLon]]:
    """Read just the scheduled shapes the report draws, in one streaming pass."""
    if not wanted:
        return {}

    collected: dict[str, list[tuple[int, LatLon]]] = {}
    with zipfile.ZipFile(zip_path) as archive:
        for row in _read_table(archive, "shapes.txt"):
            shape_id = row.get("shape_id", "")
            if shape_id not in wanted:
                continue
            try:
                sequence = int(row["shape_pt_sequence"])
                point = LatLon(float(row["shape_pt_lat"]), float(row["shape_pt_lon"]))
            except (KeyError, ValueError):
                continue
            collected.setdefault(shape_id, []).append((sequence, point))

    return {
        shape_id: [point for _, point in sorted(points)] for shape_id, points in collected.items()
    }


def _shape_for(entity: gtfs_rt.FeedEntity, shapes: dict[str, list[LatLon]]) -> list[LatLon]:
    shape_id = next(
        (s.shape_id for s in entity.trip_modifications.selected_trips if s.shape_id), None
    )
    return shapes.get(shape_id or "", [])


def _cancelled_positions(
    trip_modifications: gtfs_rt.TripModifications, pattern: Pattern
) -> set[int]:
    """Every trip position the entity's modifications claim to cancel."""
    by_sequence = {sequence: position for position, (sequence, _) in enumerate(pattern)}
    cancelled: set[int] = set()

    for modification in trip_modifications.modifications:
        if not modification.HasField("start_stop_selector"):
            continue
        start = by_sequence.get(modification.start_stop_selector.stop_sequence)
        if start is None:
            continue
        if modification.HasField("end_stop_selector"):
            end = by_sequence.get(modification.end_stop_selector.stop_sequence)
            if end is None:
                continue
        else:
            end = start
        if start <= end:
            cancelled.update(range(start, end + 1))

    return cancelled


def _panel(entity: gtfs_rt.FeedEntity, pattern: Pattern, modification_index: int) -> Panel:
    modification = entity.trip_modifications.modifications[modification_index]
    return Panel(
        cancelled_positions=_cancelled_positions(entity.trip_modifications, pattern),
        replacement_stop_ids=[stop.stop_id for stop in modification.replacement_stops],
    )


def _realtime_stops(feed: gtfs_rt.FeedMessage) -> dict[str, LatLon]:
    positions: dict[str, LatLon] = {}
    for entity in feed.entity:
        if entity.HasField("stop") and entity.stop.stop_id:
            stop = entity.stop
            if stop.HasField("stop_lat") and stop.HasField("stop_lon"):
                positions[stop.stop_id] = LatLon(stop.stop_lat, stop.stop_lon)
    return positions


def _sort_key(page: Page) -> tuple[int, str]:
    """Range extensions first — they are the substantive repairs."""
    return (0 if page.range_grew else 1, page.entity_id)


def collect(cache_dir: Path, realtime_path: Path | None = None) -> Collected:
    """Fetch, repair, and assemble one page per repaired modification."""
    settings = Settings(cache_dir=cache_dir)
    static: StaticFeed = pipeline.load_static_feed(settings)
    zip_path = cache_dir / "static-gtfs.zip"
    stop_names, trip_labels, trip_shapes = read_names(zip_path)

    if realtime_path is not None:
        payload = realtime_path.read_bytes()
    else:
        if settings.credentials is None:
            raise SystemExit(
                f"The realtime feed needs basic auth. Set {USERNAME_ENV} and "
                f"{PASSWORD_ENV}, or pass a saved feed."
            )
        payload = download.fetch_realtime(settings.realtime_url, settings.credentials)

    raw_feed = gtfs_rt.FeedMessage()
    raw_feed.ParseFromString(payload)

    result = repair_feed(raw_feed, static, settings.repair)
    pages = build_pages(raw_feed, result, static, stop_names, trip_labels)

    wanted_shapes = {
        shape_id for page in pages if (shape_id := trip_shapes.get(page.trip_id)) is not None
    }
    scheduled = read_shapes(zip_path, wanted_shapes)
    for page in pages:
        shape_id = trip_shapes.get(page.trip_id)
        page.scheduled_shape = scheduled.get(shape_id or "", [])

    pages.sort(key=_sort_key)
    return Collected(
        pages=pages,
        feed_timestamp=raw_feed.header.timestamp,
        threshold_m=settings.repair.off_shape_threshold_m,
        entities_examined=len(result.entities),
        entities_repaired=len(result.repaired_entities),
    )


def build_pages(
    raw_feed: gtfs_rt.FeedMessage,
    result: RepairResult,
    static: StaticFeed,
    stop_names: dict[str, str],
    trip_labels: TripFacts,
) -> list[Page]:
    """One page per repaired modification. Reads nothing from the network."""
    raw_by_id = {
        entity.id: entity for entity in raw_feed.entity if entity.HasField("trip_modifications")
    }
    fixed_by_id = {
        entity.id: entity for entity in result.feed.entity if entity.HasField("trip_modifications")
    }
    shapes = {
        entity.shape.shape_id: decode_polyline(entity.shape.encoded_polyline)
        for entity in raw_feed.entity
        if entity.HasField("shape") and entity.shape.shape_id and entity.shape.encoded_polyline
    }
    realtime_positions = _realtime_stops(raw_feed)

    pages: list[Page] = []
    for entity_report in result.repaired_entities:
        pages.extend(
            _pages_for_entity(
                entity_report,
                raw_by_id,
                fixed_by_id,
                shapes,
                static,
                stop_names,
                trip_labels,
                realtime_positions,
            )
        )
    return pages


def _pages_for_entity(
    entity_report: EntityReport,
    raw_by_id: dict[str, gtfs_rt.FeedEntity],
    fixed_by_id: dict[str, gtfs_rt.FeedEntity],
    shapes: dict[str, list[LatLon]],
    static: StaticFeed,
    stop_names: dict[str, str],
    trip_labels: TripFacts,
    realtime_positions: dict[str, LatLon],
) -> list[Page]:
    raw_entity = raw_by_id[entity_report.entity_id]
    fixed_entity = fixed_by_id[entity_report.entity_id]
    pattern = static.pattern_for_trip(entity_report.trip_id or "")
    shape = _shape_for(raw_entity, shapes)
    if pattern is None or len(shape) < 2:
        return []

    route = trip_labels.get(entity_report.trip_id or "", RouteLabel("", "", ""))

    positions: dict[str, LatLon] = {}
    for _, stop_id in pattern:
        if (location := static.position(stop_id)) is not None:
            positions[stop_id] = location

    pages: list[Page] = []
    for modification_report in entity_report.modifications:
        if not modification_report.changed:
            continue
        index = modification_report.index
        before = _panel(raw_entity, pattern, index)
        after = _panel(fixed_entity, pattern, index)

        for stop_id in set(before.replacement_stop_ids) | set(after.replacement_stop_ids):
            if stop_id in positions:
                continue
            location = realtime_positions.get(stop_id) or static.position(stop_id)
            if location is not None:
                positions[stop_id] = location

        pages.append(
            Page(
                entity_id=entity_report.entity_id,
                modification_index=index,
                trip_id=entity_report.trip_id or "",
                route=route,
                pattern=pattern,
                shape=shape,
                before=before,
                after=after,
                report=modification_report,
                stop_names=stop_names,
                stop_positions=positions,
            )
        )
    return pages
