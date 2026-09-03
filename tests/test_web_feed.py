"""The GTFS-RT feed the plans are written into."""

from __future__ import annotations

from google.transit import gtfs_realtime_pb2 as gtfs_rt

from fixtures import STOP_SPACING_M
from tmfix.geometry import decode_polyline
from tmweb.build import build
from tmweb.feed import build_feed
from web_fixtures import (
    DETOUR_OFFSET_M,
    SERVICE_DATE,
    detour_sections,
    line_detour,
    site_stop,
    static_feed,
)


def sample_result():
    cancelled_line, detoured_line = detour_sections(range(4, 7))
    stops = [
        site_stop(f"S{index}", (index - 1) * STOP_SPACING_M, cancelled=4 <= index <= 6)
        for index in range(1, 11)
    ]
    stops.append(site_stop("T1", 5.0 * STOP_SPACING_M, DETOUR_OFFSET_M, replacement=True))
    detour = line_detour(stops, [(cancelled_line, detoured_line)])
    return build([detour], static_feed(), SERVICE_DATE)


def test_the_feed_holds_a_stop_a_shape_and_the_modifications():
    feed = build_feed(sample_result(), SERVICE_DATE, timestamp=1788436298)

    assert feed.header.incrementality == gtfs_rt.FeedHeader.FULL_DATASET
    assert feed.header.timestamp == 1788436298
    assert [entity.id for entity in feed.entity] == [
        "web_stop_T1",
        "web_shape_entity_51_1",
        "web_detour_51_1",
    ]


def test_the_temporary_stop_carries_its_position_and_name():
    feed = build_feed(sample_result(), SERVICE_DATE, timestamp=1)

    stop = next(entity.stop for entity in feed.entity if entity.HasField("stop"))
    assert stop.stop_id == "T1"
    assert stop.stop_name.translation[0].language == "fr"
    assert stop.stop_lat > 45.5


def test_the_modifications_point_at_the_shape_and_the_service_date():
    feed = build_feed(sample_result(), SERVICE_DATE, timestamp=1)

    entity = next(entity for entity in feed.entity if entity.HasField("trip_modifications"))
    modifications = entity.trip_modifications
    assert list(modifications.service_dates) == [SERVICE_DATE]
    assert modifications.selected_trips[0].shape_id == "web_shape_51_1"
    assert list(modifications.selected_trips[0].trip_ids) == ["51_1-trip-0", "51_1-trip-1"]

    (modification,) = modifications.modifications
    assert modification.start_stop_selector.stop_sequence == 4
    assert modification.end_stop_selector.stop_sequence == 6
    assert [stop.stop_id for stop in modification.replacement_stops] == ["T1"]


def test_the_published_shape_leaves_the_scheduled_one():
    feed = build_feed(sample_result(), SERVICE_DATE, timestamp=1)

    shape = next(entity.shape for entity in feed.entity if entity.HasField("shape"))
    points = decode_polyline(shape.encoded_polyline)
    assert len(points) > 2
    assert max(point.lat for point in points) > 45.5
