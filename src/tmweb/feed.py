"""Writing the plans out as a GTFS-RT feed.

Each route pattern becomes one `TripModifications` entity, its detoured shape
one `Shape` entity, and every replacement stop the static GTFS does not have
one `Stop` entity. The IDs all carry a `web_` prefix so nothing in this feed
can be mistaken for an entity of the STM's own.
"""

from __future__ import annotations

from google.transit import gtfs_realtime_pb2 as gtfs_rt

from tmfix.geometry import encode_polyline

from .build import BuildResult, PatternPlan
from .site import SiteStop

ENTITY_PREFIX = "web_detour_"
SHAPE_PREFIX = "web_shape_"


def shape_id_for(plan: PatternPlan) -> str:
    return f"{SHAPE_PREFIX}{plan.route_pattern_id}"


def entity_id_for(plan: PatternPlan) -> str:
    return f"{ENTITY_PREFIX}{plan.route_pattern_id}"


def build_feed(result: BuildResult, service_date: str, timestamp: int) -> gtfs_rt.FeedMessage:
    """Assemble the whole feed: the stops, the shapes and the modifications."""
    feed = gtfs_rt.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.incrementality = gtfs_rt.FeedHeader.FULL_DATASET
    feed.header.timestamp = timestamp

    for stop_id in sorted(result.new_stops):
        _add_stop(feed, result.new_stops[stop_id])

    for plan in result.plans:
        if plan.shape:
            _add_shape(feed, plan)
        _add_modifications(feed, plan, service_date)

    return feed


def _add_stop(feed: gtfs_rt.FeedMessage, stop: SiteStop) -> None:
    entity = feed.entity.add()
    entity.id = f"web_stop_{stop.stop_id}"
    entity.stop.stop_id = stop.stop_id
    entity.stop.stop_lat = stop.position.lat
    entity.stop.stop_lon = stop.position.lon
    if stop.name:
        translation = entity.stop.stop_name.translation.add()
        translation.text = stop.name
        translation.language = "fr"


def _add_shape(feed: gtfs_rt.FeedMessage, plan: PatternPlan) -> None:
    entity = feed.entity.add()
    entity.id = f"web_shape_entity_{plan.route_pattern_id}"
    entity.shape.shape_id = shape_id_for(plan)
    entity.shape.encoded_polyline = encode_polyline(plan.shape or [])


def _add_modifications(
    feed: gtfs_rt.FeedMessage,
    plan: PatternPlan,
    service_date: str,
) -> None:
    entity = feed.entity.add()
    entity.id = entity_id_for(plan)
    modifications = entity.trip_modifications

    selected = modifications.selected_trips.add()
    selected.trip_ids.extend(plan.trip_ids)
    if plan.shape:
        selected.shape_id = shape_id_for(plan)
    modifications.service_dates.append(service_date)

    for planned in plan.modifications:
        modification = modifications.modifications.add()
        modification.start_stop_selector.stop_sequence = planned.start_sequence
        modification.end_stop_selector.stop_sequence = planned.end_sequence
        for stop_id in planned.replacement_stop_ids:
            modification.replacement_stops.add().stop_id = stop_id
