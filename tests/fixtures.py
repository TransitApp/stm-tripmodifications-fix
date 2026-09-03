"""Synthetic feeds for the tests.

A base route is a straight line of stops running east. A detour leaves that
line over a run of stops, runs parallel to it well to the north, and rejoins.
That is the shape of every real case this tool repairs, without shipping any
real feed data.
"""

from __future__ import annotations

import math

from google.transit import gtfs_realtime_pb2 as gtfs_rt

from tmfix.geometry import LatLon
from tmfix.static_feed import StaticFeed

BASE_LAT = 45.5
BASE_LON = -73.6

STOP_SPACING_M = 500.0
DETOUR_OFFSET_M = 800.0

_METRES_PER_DEGREE_LAT = math.pi * 6_371_008.8 / 180.0
_METRES_PER_DEGREE_LON = _METRES_PER_DEGREE_LAT * math.cos(math.radians(BASE_LAT))


def at(east_m: float, north_m: float = 0.0) -> LatLon:
    """A position the given distance east and north of the base point."""
    return LatLon(
        BASE_LAT + north_m / _METRES_PER_DEGREE_LAT,
        BASE_LON + east_m / _METRES_PER_DEGREE_LON,
    )


def encode_polyline(points: list[LatLon], precision: int = 5) -> str:
    """Encode positions as a Google encoded polyline. Only the tests need this."""
    factor = 10**precision
    output: list[str] = []
    previous = (0, 0)

    for point in points:
        current = (round(point.lat * factor), round(point.lon * factor))
        for value, last in zip(current, previous, strict=True):
            delta = value - last
            delta = ~(delta << 1) if delta < 0 else (delta << 1)
            while delta >= 0x20:
                output.append(chr((0x20 | (delta & 0x1F)) + 63))
                delta >>= 5
            output.append(chr(delta + 63))
        previous = current

    return "".join(output)


def straight_route(stop_count: int, prefix: str = "S") -> tuple[list[str], StaticFeed]:
    """A trip of evenly spaced stops running east, and a feed describing it."""
    stop_ids = [f"{prefix}{index + 1}" for index in range(stop_count)]
    feed = StaticFeed()

    for index, stop_id in enumerate(stop_ids):
        feed.stop_positions[stop_id] = at(index * STOP_SPACING_M)

    pattern = tuple((index + 1, stop_id) for index, stop_id in enumerate(stop_ids))
    feed.patterns.append(pattern)
    feed.pattern_by_trip["trip-1"] = 0

    return stop_ids, feed


def detour_shape(stop_count: int, skipped: range) -> list[LatLon]:
    """A shape that follows the route but bypasses the given stop positions.

    It leaves the line midway before the first skipped stop and rejoins midway
    after the last one, so the stops on either side stay close to it.
    """
    leave_east = (skipped.start - 0.5) * STOP_SPACING_M
    rejoin_east = (skipped.stop - 1 + 0.5) * STOP_SPACING_M

    points = [at(index * STOP_SPACING_M) for index in range(skipped.start)]
    points.append(at(leave_east))
    points.append(at(leave_east, DETOUR_OFFSET_M))
    points.append(at(rejoin_east, DETOUR_OFFSET_M))
    points.append(at(rejoin_east))
    points.extend(at(index * STOP_SPACING_M) for index in range(skipped.stop, stop_count))
    return points


def temporary_stop(stop_id: str, position: LatLon) -> gtfs_rt.FeedEntity:
    """A feed entity defining a temporary stop."""
    entity = gtfs_rt.FeedEntity(id=f"stop-{stop_id}")
    entity.stop.stop_id = stop_id
    entity.stop.stop_lat = position.lat
    entity.stop.stop_lon = position.lon
    return entity


def build_feed(
    shape_points: list[LatLon],
    modifications: list[dict],
    shape_id: str = "detour-shape",
    entity_id: str = "detour-1",
    extra_entities: list[gtfs_rt.FeedEntity] | None = None,
    trip_ids: list[str] | None = None,
) -> gtfs_rt.FeedMessage:
    """Assemble a feed with one TripModifications entity and its shape.

    Each modification is a dict with `start`, optional `end` (stop_sequence
    values), and optional `replacement_stops` (a list of stop IDs).
    """
    feed = gtfs_rt.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.incrementality = gtfs_rt.FeedHeader.FULL_DATASET

    shape_entity = feed.entity.add()
    shape_entity.id = f"shape-{shape_id}"
    shape_entity.shape.shape_id = shape_id
    shape_entity.shape.encoded_polyline = encode_polyline(shape_points)

    for entity in extra_entities or []:
        feed.entity.add().CopyFrom(entity)

    modifications_entity = feed.entity.add()
    modifications_entity.id = entity_id
    trip_modifications = modifications_entity.trip_modifications
    selected = trip_modifications.selected_trips.add()
    selected.trip_ids.extend(trip_ids or ["trip-1"])
    selected.shape_id = shape_id
    trip_modifications.service_dates.append("20260902")

    for spec in modifications:
        modification = trip_modifications.modifications.add()
        modification.start_stop_selector.stop_sequence = spec["start"]
        if "end" in spec:
            modification.end_stop_selector.stop_sequence = spec["end"]
        for stop_id in spec.get("replacement_stops", []):
            modification.replacement_stops.add().stop_id = stop_id

    return feed
