"""Turning the website's detours into modifications of static GTFS trips.

The website marks each stop of a line as skipped or as served instead, so which
stops a detour drops is read straight off the flags rather than inferred. Two
things still have to be worked out with geometry: which detour stands in for
which run of skipped stops, and what order the replacement stops are served in.
Both are settled by projecting onto the shapes the website publishes alongside
the flags.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from itertools import pairwise

from tmfix.geometry import (
    LatLon,
    Projection,
    cumulative_lengths,
    distance_between,
    project_onto,
    splice,
)
from tmfix.static_feed import Pattern, StaticFeed

from .site import LineDetour, SiteStop

logger = logging.getLogger(__name__)


@dataclass
class BuildConfig:
    """How far things are allowed to be from where they should be."""

    # A replacement stop farther than this from every detour is left out: it is
    # on a part of the line this run of the detour does not touch.
    replacement_threshold_m: float = 100.0
    # A detour whose ends are farther than this from a pattern's shape does not
    # apply to that pattern, which runs a different part of the line.
    on_shape_threshold_m: float = 60.0
    # How far apart a cancelled section and the detour taking its place may
    # start and end and still be read as the same detour.
    section_pairing_m: float = 200.0
    # How far a run of skipped stops may sit from a detour and still be the run
    # that detour stands in for.
    run_matching_m: float = 500.0


@dataclass
class ModificationPlan:
    """One `Modification`: the stops dropped, and the stops served instead."""

    start_sequence: int
    end_sequence: int
    replacement_stop_ids: list[str]
    cancelled_stop_ids: list[str]
    # A stop inside the span that the trip keeps, because the detour still
    # passes it. Set when a detour drops no stop of its own and so has to name
    # one as its span.
    anchor_stop_id: str | None = None


@dataclass
class PatternPlan:
    """Every modification that applies to one route pattern."""

    route_pattern_id: str
    route_id: str
    direction: str
    line_description: str
    trip_ids: list[str]
    modifications: list[ModificationPlan]
    shape: list[LatLon] | None


@dataclass
class SkippedLine:
    """A line the website says is detoured that produced nothing."""

    line_key: str
    reason: str


@dataclass
class BuildResult:
    plans: list[PatternPlan] = field(default_factory=list)
    # Replacement stops the static feed does not have, which the feed must define.
    new_stops: dict[str, SiteStop] = field(default_factory=dict)
    skipped: list[SkippedLine] = field(default_factory=list)
    dropped_replacements: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class _Detour:
    """One road section a line leaves, and the one it takes instead."""

    cancelled: list[LatLon] | None
    detoured: list[LatLon] | None
    stops: list[SiteStop] = field(default_factory=list)

    @property
    def reference(self) -> list[LatLon]:
        """The section to place on a trip's shape: the one it leaves if there is one."""
        return self.cancelled or self.detoured or []


def build(
    detours: list[LineDetour],
    feed: StaticFeed,
    service_date: str,
    config: BuildConfig | None = None,
) -> BuildResult:
    """Work out the modifications every detoured line implies."""
    config = config or BuildConfig()
    result = BuildResult()
    running = feed.calendar.services_on(service_date)

    for line_detour in sorted(detours, key=lambda item: item.line.key):
        _build_line(line_detour, feed, running, config, result)

    result.plans.sort(key=lambda plan: plan.route_pattern_id)
    return result


def _build_line(
    line_detour: LineDetour,
    feed: StaticFeed,
    running: set[str],
    config: BuildConfig,
    result: BuildResult,
) -> None:
    line = line_detour.line
    direction_id = feed.direction_ids.get((line.identifier, line.direction))
    if direction_id is None:
        result.skipped.append(SkippedLine(line.key, "no such route and direction in the GTFS"))
        return

    route_patterns = feed.route_patterns_by_direction.get((line.identifier, direction_id), [])
    if not route_patterns:
        result.skipped.append(SkippedLine(line.key, "the GTFS has no trips in this direction"))
        return

    detour_list = _pair_sections(line_detour, config)
    dropped = _assign_replacements(line_detour, detour_list, config)
    if dropped:
        result.dropped_replacements[line.key] = dropped

    cancelled_ids = line_detour.cancelled_stop_ids
    made_something = False

    for route_pattern_id in route_patterns:
        pattern = feed.pattern_for_route_pattern(route_pattern_id)
        shape = feed.shapes.get(route_pattern_id)
        if not pattern or not shape:
            continue
        trip_ids = [
            trip_id
            for trip_id, service_id in feed.trips_by_route_pattern.get(route_pattern_id, [])
            if service_id in running
        ]
        if not trip_ids:
            continue

        plan = _build_pattern(
            route_pattern_id=route_pattern_id,
            line_detour=line_detour,
            pattern=pattern,
            shape=shape,
            trip_ids=sorted(trip_ids),
            detours=detour_list,
            cancelled_ids=cancelled_ids,
            feed=feed,
            config=config,
        )
        if plan is None:
            continue
        made_something = True
        result.plans.append(plan)
        for modification in plan.modifications:
            for stop_id in modification.replacement_stop_ids:
                if feed.position(stop_id) is None:
                    site_stop = _find_stop(line_detour, stop_id)
                    if site_stop is not None:
                        result.new_stops[stop_id] = site_stop

    if not made_something:
        result.skipped.append(SkippedLine(line.key, "no trip pattern running today is affected"))


def _pair_sections(line_detour: LineDetour, config: BuildConfig) -> list[_Detour]:
    """Match each cancelled road section with the detour that replaces it.

    They are matched on their ends, which a detour shares exactly with the
    section it stands in for. A section with no partner is still a detour: one
    that drops stops and puts nothing in their place, or the other way round.
    """
    route = line_detour.route
    unclaimed = list(range(len(route.detoured)))
    paired: list[_Detour] = []

    for cancelled in route.cancelled:
        best_gap, best_index = None, None
        for index in unclaimed:
            detoured = route.detoured[index]
            gap = distance_between(cancelled[0], detoured[0]) + distance_between(
                cancelled[-1], detoured[-1]
            )
            if best_gap is None or gap < best_gap:
                best_gap, best_index = gap, index
        if best_index is not None and best_gap is not None and best_gap <= config.section_pairing_m:
            unclaimed.remove(best_index)
            paired.append(_Detour(cancelled=cancelled, detoured=route.detoured[best_index]))
        else:
            paired.append(_Detour(cancelled=cancelled, detoured=None))

    paired.extend(_Detour(cancelled=None, detoured=route.detoured[i]) for i in unclaimed)
    return paired


def _assign_replacements(
    line_detour: LineDetour,
    detours: list[_Detour],
    config: BuildConfig,
) -> list[str]:
    """Give every replacement stop to the detour it stands on, in travel order.

    Returns the stops that sit on none of them, which are reported rather than
    guessed at.
    """
    dropped: list[str] = []
    placed: dict[int, list[tuple[float, SiteStop]]] = {}

    for stop in line_detour.replacement_stops:
        best: tuple[float, int, Projection] | None = None
        for index, detour in enumerate(detours):
            if not detour.detoured:
                continue
            projection = project_onto(stop.position, detour.detoured)
            if best is None or projection.distance_m < best[0]:
                best = (projection.distance_m, index, projection)
        if best is None or best[0] > config.replacement_threshold_m:
            dropped.append(stop.stop_id)
            continue
        placed.setdefault(best[1], []).append((best[2].along_m, stop))

    for index, stops in placed.items():
        stops.sort(key=lambda item: item[0])
        detours[index].stops = [stop for _, stop in stops]

    return dropped


@dataclass
class _Placed:
    """A detour, and where it falls on one trip's shape."""

    detour: _Detour
    start_m: float
    end_m: float


def _place(
    detours: list[_Detour],
    shape: list[LatLon],
    config: BuildConfig,
) -> list[_Placed]:
    """Find where on a trip's shape each detour leaves it and rejoins it."""
    lengths = cumulative_lengths(shape)
    placed: list[_Placed] = []

    for detour in detours:
        reference = detour.reference
        if not reference:
            continue
        first = project_onto(reference[0], shape, lengths)
        last = project_onto(reference[-1], shape, lengths)
        if max(first.distance_m, last.distance_m) > config.on_shape_threshold_m:
            continue
        placed.append(
            _Placed(detour, min(first.along_m, last.along_m), max(first.along_m, last.along_m))
        )

    placed.sort(key=lambda item: (item.start_m, item.end_m))
    return placed


def _runs(pattern: Pattern, cancelled_ids: set[str]) -> list[list[int]]:
    """The runs of consecutive stop sequences a detour drops."""
    runs: list[list[int]] = []
    for sequence, stop_id in pattern:
        if stop_id not in cancelled_ids:
            continue
        if runs and sequence == runs[-1][-1] + 1:
            runs[-1].append(sequence)
        else:
            runs.append([sequence])
    return runs


def _build_pattern(
    *,
    route_pattern_id: str,
    line_detour: LineDetour,
    pattern: Pattern,
    shape: list[LatLon],
    trip_ids: list[str],
    detours: list[_Detour],
    cancelled_ids: set[str],
    feed: StaticFeed,
    config: BuildConfig,
) -> PatternPlan | None:
    placed = _place(detours, shape, config)
    if not placed:
        return None

    lengths = cumulative_lengths(shape)
    stop_of = dict(pattern)
    position_on_shape: dict[int, float] = {}
    for sequence, stop_id in pattern:
        position = feed.position(stop_id)
        if position is not None:
            position_on_shape[sequence] = project_onto(position, shape, lengths).along_m

    runs = _runs(pattern, cancelled_ids)
    matched = _match_runs(runs, placed, position_on_shape, config)

    modifications: list[ModificationPlan] = []
    for index, run in enumerate(runs):
        detour_index = matched.runs.get(index)
        served = placed[detour_index].detour.stops if detour_index is not None else []
        modifications.append(
            ModificationPlan(
                start_sequence=run[0],
                end_sequence=run[-1],
                replacement_stop_ids=[stop.stop_id for stop in served],
                cancelled_stop_ids=[stop_of[sequence] for sequence in run],
            )
        )

    for index, placement in enumerate(placed):
        if index in matched.claimed or not placement.detour.stops:
            continue
        insertion = _insertion(placement, pattern, position_on_shape, cancelled_ids, feed)
        if insertion is not None and not _merge_into_neighbour(modifications, insertion, stop_of):
            modifications.append(insertion)

    if not modifications:
        return None

    modifications.sort(key=lambda item: item.start_sequence)
    if _overlapping(modifications):
        # Merging above should have prevented this. Writing spans that overlap
        # would be worse than writing nothing for the pattern.
        logger.warning("%s: spans overlap, leaving the pattern out", route_pattern_id)
        return None

    return PatternPlan(
        route_pattern_id=route_pattern_id,
        route_id=line_detour.line.identifier,
        direction=line_detour.line.direction,
        line_description=line_detour.line.description,
        trip_ids=trip_ids,
        modifications=modifications,
        shape=_detoured_shape(shape, placed),
    )


@dataclass
class _Matched:
    """Which detour stands in for which run, by their index in each list."""

    runs: dict[int, int] = field(default_factory=dict)
    claimed: set[int] = field(default_factory=set)


def _match_runs(
    runs: list[list[int]],
    placed: list[_Placed],
    position_on_shape: dict[int, float],
    config: BuildConfig,
) -> _Matched:
    """Give each run of dropped stops the detour that covers it on the shape."""
    candidates = []
    for run_index, run in enumerate(runs):
        first = position_on_shape.get(run[0])
        last = position_on_shape.get(run[-1])
        if first is None or last is None:
            continue
        for detour_index, placement in enumerate(placed):
            gap = max(0.0, max(first, placement.start_m) - min(last, placement.end_m))
            # Where two detours cover the same run — the STM sometimes splits
            # one into a cancelled section and a separate served one — the one
            # naming stops is the one that describes it.
            served = 0 if placement.detour.stops else 1
            candidates.append((gap, served, run_index, detour_index))

    matched = _Matched()
    for gap, _, run_index, detour_index in sorted(candidates):
        if run_index in matched.runs or detour_index in matched.claimed:
            continue
        if gap > config.run_matching_m:
            continue
        matched.runs[run_index] = detour_index
        matched.claimed.add(detour_index)
    return matched


def _insertion(
    placement: _Placed,
    pattern: Pattern,
    position_on_shape: dict[int, float],
    cancelled_ids: set[str],
    feed: StaticFeed,
) -> ModificationPlan | None:
    """A detour that drops no stop, written as a span of the one stop it leaves from.

    A `Modification` needs a `start_stop_selector`, so an added stop cannot be
    expressed on its own. The stop the detour leaves from is named as the span
    and put back at the head of the replacement list, which leaves the trip
    calling at it exactly as before.
    """
    nearest = [
        (abs(position_on_shape[sequence] - placement.start_m), sequence)
        for sequence, stop_id in pattern
        if sequence in position_on_shape and stop_id not in cancelled_ids
    ]
    if not nearest or not placement.detour.detoured:
        return None

    anchor = min(nearest)[1]
    anchor_stop_id = dict(pattern)[anchor]
    anchor_position = feed.position(anchor_stop_id)
    if anchor_position is None:
        return None

    ordered = [
        (project_onto(stop.position, placement.detour.detoured).along_m, stop.stop_id)
        for stop in placement.detour.stops
    ]
    ordered.append(
        (project_onto(anchor_position, placement.detour.detoured).along_m, anchor_stop_id)
    )
    ordered.sort()

    return ModificationPlan(
        start_sequence=anchor,
        end_sequence=anchor,
        replacement_stop_ids=[stop_id for _, stop_id in ordered],
        cancelled_stop_ids=[],
        anchor_stop_id=anchor_stop_id,
    )


def _merge_into_neighbour(
    modifications: list[ModificationPlan],
    insertion: ModificationPlan,
    stop_of: dict[int, str],
) -> bool:
    """Fold an added-stops modification into a span it would otherwise touch.

    Spans that touch must be one modification, so when the stop a detour leaves
    from sits right beside a run of dropped stops the two become one span.
    """
    for existing in modifications:
        if insertion.start_sequence == existing.end_sequence + 1:
            existing.end_sequence = insertion.end_sequence
        elif insertion.end_sequence == existing.start_sequence - 1:
            existing.start_sequence = insertion.start_sequence
        else:
            continue
        existing.cancelled_stop_ids = [
            stop_of[sequence]
            for sequence in range(existing.start_sequence, existing.end_sequence + 1)
            if stop_of[sequence] not in insertion.replacement_stop_ids
        ]
        existing.anchor_stop_id = insertion.anchor_stop_id
        existing.replacement_stop_ids = _joined(existing, insertion)
        return True
    return False


def _joined(existing: ModificationPlan, insertion: ModificationPlan) -> list[str]:
    """The two replacement lists, in the order the trip runs them."""
    if insertion.start_sequence < existing.start_sequence:
        return insertion.replacement_stop_ids + existing.replacement_stop_ids
    return existing.replacement_stop_ids + insertion.replacement_stop_ids


def _overlapping(modifications: list[ModificationPlan]) -> bool:
    """Whether any two spans overlap or touch, which the spec forbids."""
    for earlier, later in pairwise(modifications):
        if later.start_sequence <= earlier.end_sequence + 1:
            return True
    return False


def _detoured_shape(shape: list[LatLon], placed: list[_Placed]) -> list[LatLon] | None:
    """The trip's shape with each detour spliced in where it leaves the line."""
    replacements = [
        (item.start_m, item.end_m, item.detour.detoured) for item in placed if item.detour.detoured
    ]
    if not replacements:
        return None
    return splice(shape, replacements)


def _find_stop(line_detour: LineDetour, stop_id: str) -> SiteStop | None:
    for stop in line_detour.stops:
        if stop.stop_id == stop_id:
            return stop
    return None
