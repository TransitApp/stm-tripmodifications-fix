"""Turn a published website feed back into pages to draw.

Nothing is rebuilt from stm.info here: the feed `tmweb` wrote already says
which stops each detour skips, which it serves instead, and what road it
takes. The static GTFS supplies the rest — where the stops are, the trip's
scheduled stop list and its scheduled shape — and the zip supplies the names.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from google.transit import gtfs_realtime_pb2 as gtfs_rt

from tmfix.geometry import LatLon, decode_polyline
from tmfix.static_feed import Pattern, StaticFeed

from ..collect import RouteLabel, read_names

logger = logging.getLogger(__name__)


@dataclass
class Detour:
    """One modification of one route pattern, with everything needed to draw it."""

    entity_id: str
    modification_index: int
    route: RouteLabel
    trip_id: str
    trips: int
    # How many service dates the feed writes, which is what those trips run over.
    dates: int
    pattern: Pattern
    scheduled_shape: list[LatLon]
    detour_shape: list[LatLon]
    start_sequence: int
    end_sequence: int
    # Stops of the span the detour drops, and the ones inside it the trip still
    # calls at because they are named again as replacements.
    skipped: list[tuple[int, str]]
    kept: list[tuple[int, str]]
    replacement_stop_ids: list[str]
    # Replacement stops the static feed does not have, which the feed defines itself.
    temporary_stop_ids: set[str]
    stop_names: dict[str, str]
    stop_positions: dict[str, LatLon]
    # Other route patterns of the same line carrying this same modification.
    also_patterns: list[str] = field(default_factory=list)


@dataclass
class Collected:
    """Every page, plus the run's own facts."""

    pages: list[Detour]
    feed_timestamp: int
    service_dates: list[str]
    entities: int
    modifications: int
    temporary_stops: int
    # Modifications a page could not be drawn for, because the static feed no
    # longer has the trip the feed names.
    undrawable: int = 0
    metadata: dict[str, object] = field(default_factory=dict)


def _feed_stops(feed: gtfs_rt.FeedMessage) -> dict[str, tuple[str, LatLon]]:
    """The stops the feed defines itself, which are the temporary ones."""
    stops: dict[str, tuple[str, LatLon]] = {}
    for entity in feed.entity:
        if not entity.HasField("stop") or not entity.stop.stop_id:
            continue
        stop = entity.stop
        name = next((text.text for text in stop.stop_name.translation), "")
        stops[stop.stop_id] = (name, LatLon(stop.stop_lat, stop.stop_lon))
    return stops


def _feed_shapes(feed: gtfs_rt.FeedMessage) -> dict[str, list[LatLon]]:
    return {
        entity.shape.shape_id: decode_polyline(entity.shape.encoded_polyline)
        for entity in feed.entity
        if entity.HasField("shape") and entity.shape.shape_id and entity.shape.encoded_polyline
    }


def _route_pattern_of_trip(static: StaticFeed) -> dict[str, str]:
    return {
        trip_id: route_pattern_id
        for route_pattern_id, trips in static.trips_by_route_pattern.items()
        for trip_id, _ in trips
    }


def _sort_key(page: Detour) -> tuple[int, str, str, int]:
    """Route order as a reader expects it: 10 before 105, and 105 before 105E."""
    short_name = page.route.short_name
    digits = "".join(char for char in short_name if char.isdigit())
    return (int(digits) if digits else 10_000, short_name, page.route.headsign, page.start_sequence)


def collect(
    feed_path: Path,
    static: StaticFeed,
    zip_path: Path,
    metadata: dict[str, object] | None = None,
) -> Collected:
    """Read the feed and assemble one page per modification."""
    feed = gtfs_rt.FeedMessage()
    feed.ParseFromString(feed_path.read_bytes())

    stop_names, trip_labels, _ = read_names(zip_path)
    feed_stops = _feed_stops(feed)
    # The feed names the stops it defines itself; the zip names all the others.
    names = {**stop_names, **{stop_id: name for stop_id, (name, _) in feed_stops.items() if name}}
    shapes = _feed_shapes(feed)
    route_pattern_of_trip = _route_pattern_of_trip(static)

    pages: list[Detour] = []
    undrawable = 0
    entities = 0
    modifications = 0
    service_dates: list[str] = []

    for entity in feed.entity:
        if not entity.HasField("trip_modifications"):
            continue
        entities += 1
        trip_modifications = entity.trip_modifications
        modifications += len(trip_modifications.modifications)
        for date in trip_modifications.service_dates:
            if date not in service_dates:
                service_dates.append(date)

        drawn = _pages_for_entity(
            entity,
            dates=len(trip_modifications.service_dates),
            static=static,
            shapes=shapes,
            stop_names=names,
            feed_stops=feed_stops,
            trip_labels=trip_labels,
            route_pattern_of_trip=route_pattern_of_trip,
        )
        undrawable += len(trip_modifications.modifications) - len(drawn)
        pages.extend(drawn)

    if undrawable:
        logger.info("%d modifications name a trip the static feed does not have", undrawable)

    pages = _merge_identical(pages)
    pages.sort(key=_sort_key)

    return Collected(
        pages=pages,
        feed_timestamp=feed.header.timestamp,
        service_dates=service_dates,
        entities=entities,
        modifications=modifications,
        temporary_stops=len(feed_stops),
        undrawable=undrawable,
        metadata=metadata or {},
    )


def _pages_for_entity(
    entity: gtfs_rt.FeedEntity,
    *,
    dates: int,
    static: StaticFeed,
    shapes: dict[str, list[LatLon]],
    stop_names: dict[str, str],
    feed_stops: dict[str, tuple[str, LatLon]],
    trip_labels: dict[str, RouteLabel],
    route_pattern_of_trip: dict[str, str],
) -> list[Detour]:
    trip_modifications = entity.trip_modifications
    trip_ids = [
        trip_id for selected in trip_modifications.selected_trips for trip_id in selected.trip_ids
    ]
    if not trip_ids:
        return []

    trip_id = trip_ids[0]
    pattern = static.pattern_for_trip(trip_id)
    if pattern is None:
        return []

    shape_id = next(
        (selected.shape_id for selected in trip_modifications.selected_trips if selected.shape_id),
        "",
    )
    route_pattern_id = route_pattern_of_trip.get(trip_id, "")
    scheduled_shape = static.shapes.get(route_pattern_id, [])
    route = trip_labels.get(trip_id, RouteLabel("", "", ""))

    positions: dict[str, LatLon] = {}
    for _, stop_id in pattern:
        if (location := static.position(stop_id)) is not None:
            positions[stop_id] = location

    pages: list[Detour] = []
    for index, modification in enumerate(trip_modifications.modifications):
        replacements = [stop.stop_id for stop in modification.replacement_stops]
        temporary: set[str] = set()
        for stop_id in replacements:
            if (location := static.position(stop_id)) is not None:
                positions[stop_id] = location
                continue
            if (defined := feed_stops.get(stop_id)) is not None:
                positions[stop_id] = defined[1]
                temporary.add(stop_id)

        start = modification.start_stop_selector.stop_sequence
        end = modification.end_stop_selector.stop_sequence or start
        in_span = [(sequence, stop_id) for sequence, stop_id in pattern if start <= sequence <= end]

        pages.append(
            Detour(
                entity_id=entity.id,
                modification_index=index,
                route=route,
                trip_id=trip_id,
                trips=len(trip_ids),
                dates=dates,
                pattern=pattern,
                scheduled_shape=scheduled_shape,
                detour_shape=shapes.get(shape_id, []),
                start_sequence=start,
                end_sequence=end,
                skipped=[item for item in in_span if item[1] not in replacements],
                kept=[item for item in in_span if item[1] in replacements],
                replacement_stop_ids=replacements,
                temporary_stop_ids=temporary,
                stop_names=stop_names,
                stop_positions=positions,
            )
        )
    return pages


def _merge_identical(pages: list[Detour]) -> list[Detour]:
    """One page per detour, not one per route pattern that carries it.

    A line's short turns are given the same modification with their own stop
    sequences, and the maps would be the same map. The page names the other
    patterns instead of repeating itself.
    """
    kept: dict[tuple[str, str, tuple[str, ...], tuple[str, ...]], Detour] = {}
    for page in pages:
        key = (
            page.route.short_name,
            page.route.headsign,
            tuple(stop_id for _, stop_id in page.skipped),
            tuple(page.replacement_stop_ids),
        )
        first = kept.get(key)
        if first is None:
            kept[key] = page
        elif page.trips > first.trips:
            # Keep the pattern most trips run, and remember the one it replaces.
            page.also_patterns = [*first.also_patterns, first.entity_id]
            kept[key] = page
        else:
            first.also_patterns.append(page.entity_id)
    return list(kept.values())
