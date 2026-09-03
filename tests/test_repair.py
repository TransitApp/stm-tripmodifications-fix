"""Tests for the repair rules.

Each case mirrors one measured against STM's live feed, rebuilt from synthetic
geometry so no feed data is committed.
"""

from __future__ import annotations

from fixtures import (
    DETOUR_OFFSET_M,
    at,
    build_feed,
    detour_shape,
    straight_route,
    temporary_stop,
)
from tmfix.repair import RepairConfig, SkipReason, repair_feed
from tmfix.static_feed import StaticFeed


def ranges_of(feed) -> list[tuple[int, int]]:
    """The cancelled range of every modification, as stop_sequence bounds."""
    entity = next(e for e in feed.entity if e.HasField("trip_modifications"))
    return [
        (
            modification.start_stop_selector.stop_sequence,
            modification.end_stop_selector.stop_sequence,
        )
        for modification in entity.trip_modifications.modifications
    ]


def replacement_stops_of(feed, index: int = 0) -> list[str]:
    entity = next(e for e in feed.entity if e.HasField("trip_modifications"))
    return [
        stop.stop_id for stop in entity.trip_modifications.modifications[index].replacement_stops
    ]


# Positions 0-15 of a 20-stop route are skipped, but only stop_sequence 1 is
# declared cancelled. Matches detour_97_E_18_61937-64739.
def test_extends_a_long_run_at_the_start_of_the_line():
    _, static = straight_route(20)
    shape = detour_shape(20, range(0, 16))
    temporary = temporary_stop("T1", at(1000, DETOUR_OFFSET_M))
    feed = build_feed(
        shape,
        [{"start": 1, "end": 1, "replacement_stops": ["T1", "S1"]}],
        extra_entities=[temporary],
    )

    result = repair_feed(feed, static)

    assert ranges_of(result.feed) == [(1, 16)]
    # S1 is on the abandoned stretch, so it cannot stay as a replacement.
    assert replacement_stops_of(result.feed) == ["T1"]

    report = result.entities[0].modifications[0]
    assert report.declared_range == (1, 1)
    assert report.repaired_range == (1, 16)
    assert [stop.stop_sequence for stop in report.added_stops] == list(range(2, 17))
    assert [stop.stop_id for stop in report.dropped_stops] == ["S1"]


# Three stops skipped at the start, one declared. Matches detour_66_N_13_60625.
def test_extends_a_short_run_at_the_start_of_the_line():
    _, static = straight_route(10)
    shape = detour_shape(10, range(0, 3))
    temporary = temporary_stop("T1", at(500, DETOUR_OFFSET_M))
    feed = build_feed(
        shape,
        [{"start": 1, "end": 1, "replacement_stops": ["T1", "S1"]}],
        extra_entities=[temporary],
    )

    result = repair_feed(feed, static)

    assert ranges_of(result.feed) == [(1, 3)]
    assert replacement_stops_of(result.feed) == ["T1"]


# Positions 29-36 of a 38-stop route are skipped, only the last is declared.
# The range has to grow backwards. Matches detour_166_S_17_69336.
def test_extends_backwards_towards_the_end_of_the_line():
    _, static = straight_route(38)
    shape = detour_shape(38, range(29, 37))
    feed = build_feed(shape, [{"start": 37, "end": 37}])

    result = repair_feed(feed, static)

    assert ranges_of(result.feed) == [(30, 37)]
    report = result.entities[0].modifications[0]
    assert [stop.stop_sequence for stop in report.added_stops] == list(range(30, 37))


# A correct mid-line detour must come through untouched.
def test_a_correct_mid_line_detour_is_left_alone():
    _, static = straight_route(20)
    shape = detour_shape(20, range(4, 8))
    temporary = temporary_stop("T1", at(2500, DETOUR_OFFSET_M))
    feed = build_feed(
        shape,
        [{"start": 5, "end": 8, "replacement_stops": ["T1"]}],
        extra_entities=[temporary],
    )

    result = repair_feed(feed, static)

    assert ranges_of(result.feed) == [(5, 8)]
    assert replacement_stops_of(result.feed) == ["T1"]
    assert not result.entities[0].changed


def test_a_replacement_stop_off_the_detour_is_dropped():
    _, static = straight_route(20)
    shape = detour_shape(20, range(4, 8))
    on_route = temporary_stop("T_ON", at(2500, DETOUR_OFFSET_M))
    far_away = temporary_stop("T_FAR", at(2500, -3000))
    feed = build_feed(
        shape,
        [{"start": 5, "end": 8, "replacement_stops": ["T_ON", "T_FAR"]}],
        extra_entities=[on_route, far_away],
    )

    result = repair_feed(feed, static)

    assert replacement_stops_of(result.feed) == ["T_ON"]
    assert [stop.stop_id for stop in result.entities[0].modifications[0].dropped_stops] == ["T_FAR"]


def test_a_replacement_stop_with_no_known_position_is_kept():
    _, static = straight_route(20)
    shape = detour_shape(20, range(4, 8))
    feed = build_feed(shape, [{"start": 5, "end": 8, "replacement_stops": ["UNKNOWN"]}])

    result = repair_feed(feed, static)

    assert replacement_stops_of(result.feed) == ["UNKNOWN"]


def test_a_range_never_grows_into_another_modification():
    _, static = straight_route(20)
    # One long abandoned stretch, described by two modifications that between
    # them already cover it. Neither may swallow the other's stops.
    shape = detour_shape(20, range(4, 12))
    feed = build_feed(
        shape,
        [{"start": 5, "end": 8}, {"start": 9, "end": 12}],
    )

    result = repair_feed(feed, static)

    assert ranges_of(result.feed) == [(5, 8), (9, 12)]


def test_travel_times_stop_the_range_from_growing_backwards():
    _, static = straight_route(38)
    shape = detour_shape(38, range(29, 37))
    feed = build_feed(shape, [{"start": 37, "end": 37, "replacement_stops": ["UNKNOWN"]}])

    entity = next(e for e in feed.entity if e.HasField("trip_modifications"))
    entity.trip_modifications.modifications[0].replacement_stops[0].travel_time_to_stop = 120

    result = repair_feed(feed, static)

    assert ranges_of(result.feed) == [(37, 37)]
    assert result.entities[0].modifications[0].kept_start


def test_an_entity_with_no_shape_is_passed_through():
    _, static = straight_route(10)
    feed = build_feed(detour_shape(10, range(0, 3)), [{"start": 1, "end": 1}])
    for entity in feed.entity:
        if entity.HasField("shape"):
            entity.shape.shape_id = "something-else"

    result = repair_feed(feed, static)

    assert ranges_of(result.feed) == [(1, 1)]
    assert result.entities[0].skipped is SkipReason.NO_SHAPE


def test_an_entity_with_no_matching_trip_is_passed_through():
    feed = build_feed(detour_shape(10, range(0, 3)), [{"start": 1, "end": 1}])

    result = repair_feed(feed, StaticFeed())

    assert ranges_of(result.feed) == [(1, 1)]
    assert result.entities[0].skipped is SkipReason.NO_MATCHING_TRIP


def test_trips_with_different_stop_patterns_are_passed_through():
    _, static = straight_route(10)
    # A second trip whose stop_sequence numbers mean different stops.
    static.patterns.append(tuple((sequence, f"X{sequence}") for sequence in range(1, 11)))
    static.pattern_by_trip["trip-2"] = 1
    for sequence in range(1, 11):
        static.stop_positions[f"X{sequence}"] = at(sequence * 500, 5000)

    feed = build_feed(
        detour_shape(10, range(0, 3)),
        [{"start": 1, "end": 1}],
        trip_ids=["trip-1", "trip-2"],
    )

    result = repair_feed(feed, static)

    assert ranges_of(result.feed) == [(1, 1)]
    assert result.entities[0].skipped is SkipReason.MIXED_STOP_PATTERNS


def test_the_repair_never_removes_an_entity_or_a_modification():
    _, static = straight_route(20)
    shape = detour_shape(20, range(0, 16))
    feed = build_feed(
        shape,
        [{"start": 1, "end": 1, "replacement_stops": ["S1"]}, {"start": 19, "end": 20}],
    )

    result = repair_feed(feed, static)

    entity = next(e for e in result.feed.entity if e.HasField("trip_modifications"))
    assert len(entity.trip_modifications.modifications) == 2
    assert sum(1 for e in result.feed.entity if e.HasField("trip_modifications")) == 1


def test_the_input_feed_is_not_modified():
    _, static = straight_route(10)
    feed = build_feed(detour_shape(10, range(0, 3)), [{"start": 1, "end": 1}])

    repair_feed(feed, static)

    assert ranges_of(feed) == [(1, 1)]


def test_a_higher_threshold_stops_the_repair():
    _, static = straight_route(10)
    shape = detour_shape(10, range(0, 3))
    feed = build_feed(shape, [{"start": 1, "end": 1}])

    result = repair_feed(feed, static, RepairConfig(off_shape_threshold_m=5000.0))

    assert ranges_of(result.feed) == [(1, 1)]
    assert not result.entities[0].changed


def test_a_missing_end_selector_is_added_when_the_range_grows():
    _, static = straight_route(10)
    shape = detour_shape(10, range(0, 3))
    feed = build_feed(shape, [{"start": 1}])

    result = repair_feed(feed, static)

    entity = next(e for e in result.feed.entity if e.HasField("trip_modifications"))
    modification = entity.trip_modifications.modifications[0]
    assert modification.HasField("end_stop_selector")
    assert modification.end_stop_selector.stop_sequence == 3


def test_a_repair_that_would_empty_the_trip_is_refused():
    _, static = straight_route(4)
    # The whole route is bypassed and nothing replaces it, so extending the
    # range would leave a trip that goes nowhere.
    shape = detour_shape(4, range(0, 4))
    feed = build_feed(shape, [{"start": 1, "end": 1}])

    result = repair_feed(feed, static)

    assert ranges_of(result.feed) == [(1, 1)]
    assert result.entities[0].modifications[0].skipped is SkipReason.TOO_FEW_STOPS_LEFT
