"""Repair TripModifications whose cancelled range is shorter than the detour.

A producer sometimes describes a detour by cancelling a single stop and listing
replacement stops, even when its own detour shape skips a longer run of stops.
The stops in between stay in the trip although the bus never reaches them, and
a stop the bus no longer serves can be re-added as a replacement.

The detour shape ships in the same feed entity, so it settles what the vehicle
actually does. Two repairs follow from it:

1. Extend the cancelled range through the contiguous run of stops that lie far
   from the detour shape and touch the range the producer declared.
2. Drop replacement stops that lie far from the detour shape.

Both are deliberately conservative. Anything ambiguous is passed through
unchanged and recorded, because a modification we do not understand is still
better than no modification at all.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from google.transit import gtfs_realtime_pb2 as gtfs_rt

from .geometry import LatLon, decode_polyline, distance_to_polyline
from .static_feed import Pattern, StaticFeed


@dataclass(frozen=True)
class RepairConfig:
    """Thresholds controlling how aggressive the repair is."""

    # A stop farther than this from the detour shape is taken to be unserved.
    # Stops the vehicle does serve measure a few metres; the errors this fixes
    # measure hundreds.
    off_shape_threshold_m: float = 100.0

    # Never extend a cancellation to the point where the trip has fewer stops
    # left than this.
    min_stops_remaining: int = 2


class SkipReason(StrEnum):
    """Why a modification was passed through instead of repaired."""

    NO_SHAPE = "no detour shape in the feed"
    DEGENERATE_SHAPE = "detour shape has fewer than two points"
    NO_MATCHING_TRIP = "no selected trip is in the static feed"
    MIXED_STOP_PATTERNS = "selected trips do not share one stop pattern"
    NO_START_SELECTOR = "modification has no start_stop_selector"
    UNRESOLVED_SELECTOR = "a stop selector does not resolve to one stop of the trip"
    TOO_FEW_STOPS_LEFT = "extending the range would leave too few stops"


@dataclass
class DroppedStop:
    """A replacement stop removed because it is not on the detour shape."""

    stop_id: str
    distance_m: float


@dataclass
class AddedStop:
    """A stop added to the cancelled range because it is not on the detour shape."""

    stop_sequence: int
    stop_id: str
    distance_m: float


@dataclass
class ModificationReport:
    """What happened to one modification."""

    index: int
    declared_range: tuple[int, int] | None = None
    repaired_range: tuple[int, int] | None = None
    added_stops: list[AddedStop] = field(default_factory=list)
    dropped_stops: list[DroppedStop] = field(default_factory=list)
    skipped: SkipReason | None = None
    # True when the range could only grow forward, because the replacement
    # stops carry travel times measured from the stop before the range.
    kept_start: bool = False

    @property
    def changed(self) -> bool:
        return bool(self.added_stops or self.dropped_stops)


@dataclass
class EntityReport:
    """What happened to one feed entity."""

    entity_id: str
    shape_id: str | None = None
    trip_id: str | None = None
    modifications: list[ModificationReport] = field(default_factory=list)
    skipped: SkipReason | None = None

    @property
    def changed(self) -> bool:
        return any(report.changed for report in self.modifications)


@dataclass
class RepairResult:
    """The repaired feed and an account of every entity in it."""

    feed: gtfs_rt.FeedMessage
    entities: list[EntityReport] = field(default_factory=list)

    @property
    def repaired_entities(self) -> list[EntityReport]:
        return [entity for entity in self.entities if entity.changed]

    @property
    def skipped_entities(self) -> list[EntityReport]:
        return [
            entity
            for entity in self.entities
            if entity.skipped or any(report.skipped for report in entity.modifications)
        ]


def _collect_shapes(feed: gtfs_rt.FeedMessage) -> dict[str, list[LatLon]]:
    """Decode every shape carried in the feed, keyed by shape_id."""
    shapes: dict[str, list[LatLon]] = {}
    for entity in feed.entity:
        if not entity.HasField("shape"):
            continue
        shape = entity.shape
        if not shape.shape_id or not shape.encoded_polyline:
            continue
        try:
            shapes[shape.shape_id] = decode_polyline(shape.encoded_polyline)
        except ValueError:
            continue
    return shapes


def _collect_realtime_stops(feed: gtfs_rt.FeedMessage) -> dict[str, LatLon]:
    """Positions of the temporary stops the feed defines itself."""
    positions: dict[str, LatLon] = {}
    for entity in feed.entity:
        if not entity.HasField("stop"):
            continue
        stop = entity.stop
        if stop.stop_id and stop.HasField("stop_lat") and stop.HasField("stop_lon"):
            positions[stop.stop_id] = LatLon(stop.stop_lat, stop.stop_lon)
    return positions


def _resolve_selector(selector: gtfs_rt.StopSelector, pattern: Pattern) -> int | None:
    """Position of a stop selector within a trip's stop list, or None.

    A selector with a stop_sequence is matched on that. A selector with only a
    stop_id is matched on the stop, and rejected when the trip calls at that
    stop more than once, because then the position is ambiguous.
    """
    if selector.HasField("stop_sequence"):
        for position, (sequence, _) in enumerate(pattern):
            if sequence == selector.stop_sequence:
                return position
        return None

    if selector.stop_id:
        matches = [
            position for position, (_, stop_id) in enumerate(pattern) if stop_id == selector.stop_id
        ]
        return matches[0] if len(matches) == 1 else None

    return None


def _shared_pattern(
    trip_modifications: gtfs_rt.TripModifications, static: StaticFeed
) -> tuple[Pattern | None, str | None, bool]:
    """The stop pattern shared by the selected trips.

    Returns the pattern, the trip it came from, and whether the selected trips
    that the static feed knows about all agree on it. A stop_sequence means a
    different stop in each pattern, so a repair is only safe when they agree.
    """
    found: Pattern | None = None
    found_trip: str | None = None
    consistent = True

    for selected in trip_modifications.selected_trips:
        for trip_id in selected.trip_ids:
            pattern = static.pattern_for_trip(trip_id)
            if pattern is None:
                continue
            if found is None:
                found, found_trip = pattern, trip_id
            elif pattern != found:
                consistent = False

    return found, found_trip, consistent


def _off_shape_positions(
    pattern: Pattern,
    shape: Sequence[LatLon],
    static: StaticFeed,
    threshold_m: float,
) -> dict[int, float]:
    """Positions in the trip whose stop lies farther than the threshold from the shape.

    A stop with no known position is left out: absence of evidence is not
    evidence that the vehicle skips it.
    """
    off_shape: dict[int, float] = {}
    for position, (_, stop_id) in enumerate(pattern):
        location = static.position(stop_id)
        if location is None:
            continue
        distance = distance_to_polyline(location, shape)
        if distance > threshold_m:
            off_shape[position] = distance
    return off_shape


def _declared_positions(
    trip_modifications: gtfs_rt.TripModifications, pattern: Pattern
) -> list[tuple[int, int] | None]:
    """Resolve every modification's declared range to positions in the trip."""
    ranges: list[tuple[int, int] | None] = []
    for modification in trip_modifications.modifications:
        if not modification.HasField("start_stop_selector"):
            ranges.append(None)
            continue
        start = _resolve_selector(modification.start_stop_selector, pattern)
        if start is None:
            ranges.append(None)
            continue
        if modification.HasField("end_stop_selector"):
            end = _resolve_selector(modification.end_stop_selector, pattern)
            if end is None:
                ranges.append(None)
                continue
        else:
            end = start
        ranges.append((start, end) if start <= end else None)
    return ranges


def _extend_range(
    declared: tuple[int, int],
    off_shape: dict[int, float],
    blocked: set[int],
    pattern_length: int,
    allow_backward: bool = True,
) -> tuple[int, int]:
    """Grow a range through neighbouring stops that are not on the detour shape."""
    start, end = declared

    if allow_backward:
        while start - 1 >= 0 and (start - 1) in off_shape and (start - 1) not in blocked:
            start -= 1
    while end + 1 < pattern_length and (end + 1) in off_shape and (end + 1) not in blocked:
        end += 1

    return start, end


def _has_travel_times(modification: gtfs_rt.TripModifications.Modification) -> bool:
    """Whether any replacement stop states its own travel time.

    Those times are counted from the last stop served before the range, so
    moving the start of the range would silently change what they mean.
    """
    return any(
        replacement.HasField("travel_time_to_stop")
        for replacement in modification.replacement_stops
    )


def _apply_range(
    modification: gtfs_rt.TripModifications.Modification,
    pattern: Pattern,
    new_range: tuple[int, int],
) -> None:
    """Write a repaired range back into the selectors.

    Only the fields the producer already set are updated. Adding a stop_id to a
    selector that had only a stop_sequence would change how consumers match it.
    """
    start_position, end_position = new_range
    start_sequence, start_stop = pattern[start_position]
    end_sequence, end_stop = pattern[end_position]

    start_selector = modification.start_stop_selector
    if start_selector.HasField("stop_sequence"):
        start_selector.stop_sequence = start_sequence
    if start_selector.stop_id:
        start_selector.stop_id = start_stop

    if not modification.HasField("end_stop_selector"):
        # The producer gave no end selector, so the range was one stop. Now that
        # it is longer the end has to be stated, matching the start's style.
        if start_selector.HasField("stop_sequence"):
            modification.end_stop_selector.stop_sequence = end_sequence
        if start_selector.stop_id:
            modification.end_stop_selector.stop_id = end_stop
        return

    end_selector = modification.end_stop_selector
    if end_selector.HasField("stop_sequence"):
        end_selector.stop_sequence = end_sequence
    if end_selector.stop_id:
        end_selector.stop_id = end_stop


def _repair_entity(
    entity: gtfs_rt.FeedEntity,
    static: StaticFeed,
    shapes: dict[str, list[LatLon]],
    realtime_stops: dict[str, LatLon],
    config: RepairConfig,
) -> EntityReport:
    """Repair one TripModifications entity in place."""
    trip_modifications = entity.trip_modifications
    report = EntityReport(entity_id=entity.id)

    shape_id = next(
        (selected.shape_id for selected in trip_modifications.selected_trips if selected.shape_id),
        None,
    )
    report.shape_id = shape_id

    if shape_id is None or shape_id not in shapes:
        report.skipped = SkipReason.NO_SHAPE
        return report

    shape = shapes[shape_id]
    if len(shape) < 2:
        report.skipped = SkipReason.DEGENERATE_SHAPE
        return report

    pattern, trip_id, consistent = _shared_pattern(trip_modifications, static)
    report.trip_id = trip_id
    if pattern is None:
        report.skipped = SkipReason.NO_MATCHING_TRIP
        return report
    if not consistent:
        report.skipped = SkipReason.MIXED_STOP_PATTERNS
        return report

    off_shape = _off_shape_positions(pattern, shape, static, config.off_shape_threshold_m)
    declared_ranges = _declared_positions(trip_modifications, pattern)

    # Every position another modification already claims. A repair must not grow
    # into one, because the spec forbids modifications with overlapping spans.
    claimed: set[int] = set()
    for declared in declared_ranges:
        if declared is not None:
            claimed.update(range(declared[0], declared[1] + 1))

    def stop_position(stop_id: str) -> LatLon | None:
        return realtime_stops.get(stop_id) or static.position(stop_id)

    for index, modification in enumerate(trip_modifications.modifications):
        modification_report = ModificationReport(index=index)
        report.modifications.append(modification_report)

        # Repair 2 is independent of the range, so it runs even when the range
        # cannot be resolved.
        kept = []
        for replacement in modification.replacement_stops:
            location = stop_position(replacement.stop_id)
            if location is None:
                kept.append(replacement)
                continue
            distance = distance_to_polyline(location, shape)
            if distance > config.off_shape_threshold_m:
                modification_report.dropped_stops.append(
                    DroppedStop(stop_id=replacement.stop_id, distance_m=distance)
                )
            else:
                kept.append(replacement)

        if modification_report.dropped_stops:
            del modification.replacement_stops[:]
            modification.replacement_stops.extend(kept)

        declared = declared_ranges[index]
        if declared is None:
            modification_report.skipped = (
                SkipReason.NO_START_SELECTOR
                if not modification.HasField("start_stop_selector")
                else SkipReason.UNRESOLVED_SELECTOR
            )
            continue

        modification_report.declared_range = (
            pattern[declared[0]][0],
            pattern[declared[1]][0],
        )

        blocked = claimed - set(range(declared[0], declared[1] + 1))
        allow_backward = not _has_travel_times(modification)
        modification_report.kept_start = not allow_backward
        repaired = _extend_range(declared, off_shape, blocked, len(pattern), allow_backward)

        if repaired == declared:
            modification_report.repaired_range = modification_report.declared_range
            continue

        cancelled_after = len(claimed | set(range(repaired[0], repaired[1] + 1)))
        replacement_count = sum(
            len(other.replacement_stops) for other in trip_modifications.modifications
        )
        if len(pattern) - cancelled_after + replacement_count < config.min_stops_remaining:
            modification_report.skipped = SkipReason.TOO_FEW_STOPS_LEFT
            modification_report.repaired_range = modification_report.declared_range
            continue

        for position in range(repaired[0], repaired[1] + 1):
            if position < declared[0] or position > declared[1]:
                sequence, stop_id = pattern[position]
                modification_report.added_stops.append(
                    AddedStop(
                        stop_sequence=sequence,
                        stop_id=stop_id,
                        distance_m=off_shape[position],
                    )
                )

        _apply_range(modification, pattern, repaired)
        claimed.update(range(repaired[0], repaired[1] + 1))
        declared_ranges[index] = repaired
        modification_report.repaired_range = (
            pattern[repaired[0]][0],
            pattern[repaired[1]][0],
        )

    return report


def repair_feed(
    feed: gtfs_rt.FeedMessage,
    static: StaticFeed,
    config: RepairConfig | None = None,
) -> RepairResult:
    """Repair every TripModifications entity in a feed.

    The feed is copied, so the caller's message is untouched. Every entity and
    every modification in the input appears in the output; the repair narrows
    what a modification claims, it never removes one.
    """
    config = config or RepairConfig()

    repaired = gtfs_rt.FeedMessage()
    repaired.CopyFrom(feed)

    shapes = _collect_shapes(repaired)
    realtime_stops = _collect_realtime_stops(repaired)

    result = RepairResult(feed=repaired)
    for entity in repaired.entity:
        if not entity.HasField("trip_modifications"):
            continue
        result.entities.append(_repair_entity(entity, static, shapes, realtime_stops, config))

    return result
