"""What the website's flags and shapes turn into."""

from __future__ import annotations

from fixtures import STOP_SPACING_M, at
from tmfix.static_feed import Calendar
from tmweb.build import BuildConfig, build
from tmweb.config import service_dates_from
from web_fixtures import (
    DETOUR_OFFSET_M,
    SERVICE_DATES,
    add_pattern,
    detour_sections,
    line_detour,
    site_stop,
    static_feed,
)

# How far a moved terminus sits from the one it replaces, as the website's own
# cases do: near enough that the two sections still pair on their other end.
TERMINUS_SHIFT_M = 150.0


def scheduled_stops(stop_count: int = 10, cancelled: range | None = None) -> list:
    """The line's stop list, with the given stops marked as skipped."""
    cancelled = cancelled or range(0)
    return [
        site_stop(
            f"S{index}",
            (index - 1) * STOP_SPACING_M,
            cancelled=index in cancelled,
        )
        for index in range(1, stop_count + 1)
    ]


def one_detour(skipped: range, replacements: list | None = None, stop_count: int = 10):
    """A line whose detour skips a run of stops and serves the given ones instead."""
    cancelled_line, detoured_line = detour_sections(skipped)
    stops = scheduled_stops(stop_count, skipped) + (replacements or [])
    return line_detour(stops, [(cancelled_line, detoured_line)], stop_count)


def test_cancelled_flags_become_the_span():
    feed = static_feed()
    result = build([one_detour(range(4, 7))], feed, SERVICE_DATES)

    (plan,) = result.plans
    (modification,) = plan.modifications
    assert (modification.start_sequence, modification.end_sequence) == (4, 6)
    assert modification.cancelled_stop_ids == ["S4", "S5", "S6"]
    assert plan.trip_ids == ["51_1-trip-0", "51_1-trip-1"]


def test_replacement_stops_are_ordered_along_the_detour():
    # Listed out of order, as the website appends them to the end of its list.
    replacements = [
        site_stop("T2", 5.5 * STOP_SPACING_M, DETOUR_OFFSET_M, replacement=True),
        site_stop("T1", 4.5 * STOP_SPACING_M, DETOUR_OFFSET_M, replacement=True),
    ]
    result = build([one_detour(range(4, 7), replacements)], static_feed(), SERVICE_DATES)

    (plan,) = result.plans
    assert plan.modifications[0].replacement_stop_ids == ["T1", "T2"]
    assert set(result.new_stops) == {"T1", "T2"}


def test_a_replacement_stop_off_every_detour_is_left_out():
    far = [site_stop("T1", 4.5 * STOP_SPACING_M, DETOUR_OFFSET_M * 3, replacement=True)]
    result = build([one_detour(range(4, 7), far)], static_feed(), SERVICE_DATES)

    (plan,) = result.plans
    assert plan.modifications[0].replacement_stop_ids == []
    assert result.dropped_replacements == {"51E": ["T1"]}


def test_the_shape_follows_the_detour():
    result = build([one_detour(range(4, 7))], static_feed(), SERVICE_DATES)

    (plan,) = result.plans
    assert plan.shape is not None
    assert max(point.lat for point in plan.shape) > max(
        point.lat for point in static_feed().shapes["51_1"]
    )


def test_a_detour_that_takes_no_new_road_keeps_the_scheduled_shape():
    # The website marks the stops as skipped but publishes no road to take
    # instead: the bus runs the same way and passes them without stopping.
    cancelled_line, _ = detour_sections(range(4, 7))
    detour = line_detour(scheduled_stops(10, range(4, 7)), [(cancelled_line, None)])

    (plan,) = build([detour], static_feed(), SERVICE_DATES).plans

    assert plan.modifications[0].cancelled_stop_ids == ["S4", "S5", "S6"]
    assert plan.shape == static_feed().shapes["51_1"]


def test_a_detour_that_drops_nothing_names_the_stop_it_leaves_from():
    _, detoured_line = detour_sections(range(4, 7))
    added = site_stop("T1", 5.0 * STOP_SPACING_M, DETOUR_OFFSET_M, replacement=True)
    detour = line_detour([*scheduled_stops(), added], [(None, detoured_line)])

    result = build([detour], static_feed(), SERVICE_DATES)

    (plan,) = result.plans
    (modification,) = plan.modifications
    assert modification.start_sequence == modification.end_sequence
    assert modification.anchor_stop_id is not None
    # The stop named as the span is served again, so the trip still calls at it.
    assert modification.anchor_stop_id in modification.replacement_stop_ids


def test_a_short_turn_that_serves_a_cancelled_stop_is_modified_too():
    feed = static_feed()
    add_pattern(feed, "51_2", ["S3", "S4", "S5", "S6", "S7", "S8", "S9", "S10"])

    result = build([one_detour(range(5, 8))], feed, SERVICE_DATES)

    spans = {
        plan.route_pattern_id: [
            (item.start_sequence, item.end_sequence) for item in plan.modifications
        ]
        for plan in result.plans
    }
    assert spans == {"51_1": [(5, 7)], "51_2": [(3, 5)]}


def test_a_pattern_with_no_trip_on_any_of_the_dates_is_left_out():
    feed = static_feed()
    feed.calendar = Calendar(weekly={"weekday": ("20260101", "20260102", (True,) * 7)})

    result = build([one_detour(range(4, 7))], feed, SERVICE_DATES)

    assert result.plans == []
    assert [item.reason for item in result.skipped] == [
        "no trip pattern running on these dates is affected"
    ]


def test_a_pattern_running_only_later_in_the_week_is_still_modified():
    # A service that starts three days out: today alone finds nothing.
    feed = static_feed()
    feed.calendar = Calendar(weekly={"weekday": ("20260906", "20271231", (True,) * 7)})

    today = build([one_detour(range(4, 7))], feed, SERVICE_DATES)
    week = build([one_detour(range(4, 7))], feed, service_dates_from(SERVICE_DATES[0], 7))

    assert today.plans == []
    assert [plan.route_pattern_id for plan in week.plans] == ["51_1"]


def test_a_line_the_gtfs_does_not_have_is_reported():
    detour = one_detour(range(4, 7))
    result = build([detour], static_feed(route_id="52", direction_id="0"), SERVICE_DATES)

    assert result.plans == []
    assert result.skipped[0].reason == "no such route and direction in the GTFS"


def test_an_added_stop_beside_a_dropped_run_becomes_one_span():
    # Spans that touch must be one modification, so the two are folded together.
    cancelled_line, detoured_line = detour_sections(range(5, 7))
    added_line = detour_sections(range(8, 9))[1]
    added = site_stop("T1", 7.0 * STOP_SPACING_M, DETOUR_OFFSET_M, replacement=True)
    stops = [*scheduled_stops(10, range(5, 8)), added]
    detour = line_detour(stops, [(cancelled_line, detoured_line), (None, added_line)])

    result = build([detour], static_feed(), SERVICE_DATES)

    (plan,) = result.plans
    (modification,) = plan.modifications
    assert (modification.start_sequence, modification.end_sequence) == (5, 8)
    assert "S8" in modification.replacement_stop_ids


def test_the_replacement_threshold_is_configurable():
    replacements = [
        site_stop("T1", 5.0 * STOP_SPACING_M, DETOUR_OFFSET_M + 150.0, replacement=True)
    ]
    detour = one_detour(range(4, 7), replacements)

    strict = build([detour], static_feed(), SERVICE_DATES, BuildConfig())
    loose = build([detour], static_feed(), SERVICE_DATES, BuildConfig(replacement_threshold_m=300))

    assert strict.plans[0].modifications[0].replacement_stop_ids == []
    assert loose.plans[0].modifications[0].replacement_stop_ids == ["T1"]


def test_a_detour_at_the_start_of_the_line_moves_where_the_shape_begins():
    # The website's own terminus point lands a metre inside the GTFS shape, so
    # the section it leaves starts just short of the shape's first vertex.
    cancelled_line = [at(1.0), at(0.5 * STOP_SPACING_M)]
    detoured_line = [at(0.0, TERMINUS_SHIFT_M), at(0.5 * STOP_SPACING_M)]
    moved = site_stop("T1", 0.0, TERMINUS_SHIFT_M, replacement=True)
    stops = [*scheduled_stops(10, range(1, 2)), moved]
    detour = line_detour(stops, [(cancelled_line, detoured_line)])

    (plan,) = build([detour], static_feed(), SERVICE_DATES).plans

    assert plan.modifications[0].replacement_stop_ids == ["T1"]
    assert plan.shape is not None
    # The old terminus is gone: none of the scheduled shape is left in front of
    # the detour, which would be drawn as a spike between the two termini.
    assert plan.shape[0] == detoured_line[0]


def test_a_detour_at_the_end_of_the_line_moves_where_the_shape_ends():
    end = 10 * STOP_SPACING_M
    cancelled_line = [at(9.5 * STOP_SPACING_M), at(end - 1.0)]
    detoured_line = [at(9.5 * STOP_SPACING_M), at(end, TERMINUS_SHIFT_M)]
    moved = site_stop("T1", end, TERMINUS_SHIFT_M, replacement=True)
    stops = [*scheduled_stops(10, range(10, 11)), moved]
    detour = line_detour(stops, [(cancelled_line, detoured_line)])

    (plan,) = build([detour], static_feed(), SERVICE_DATES).plans

    assert plan.shape is not None
    assert plan.shape[-1] == detoured_line[-1]


def test_a_detour_published_backwards_is_turned_around():
    # The website sometimes lists a section against the line's direction.
    cancelled_line, detoured_line = detour_sections(range(4, 7))
    replacements = [
        site_stop("T1", 4.5 * STOP_SPACING_M, DETOUR_OFFSET_M, replacement=True),
        site_stop("T2", 5.5 * STOP_SPACING_M, DETOUR_OFFSET_M, replacement=True),
    ]
    stops = scheduled_stops(10, range(4, 7)) + replacements
    forwards = line_detour(stops, [(cancelled_line, detoured_line)])
    backwards = line_detour(stops, [(cancelled_line, list(reversed(detoured_line)))])

    (expected,) = build([forwards], static_feed(), SERVICE_DATES).plans
    (plan,) = build([backwards], static_feed(), SERVICE_DATES).plans

    # Read as published, the detour would run west over itself and the stops on
    # it would be served in reverse.
    assert plan.modifications[0].replacement_stop_ids == ["T1", "T2"]
    assert plan.shape == expected.shape
