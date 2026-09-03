"""What the report says about a run."""

from __future__ import annotations

from datetime import UTC, datetime

from fixtures import STOP_SPACING_M
from tmweb.build import BuildResult, SkippedLine, build
from tmweb.config import service_dates_from
from tmweb.due import is_due, is_due_file
from tmweb.report import build_json, build_markdown
from web_fixtures import (
    DETOUR_OFFSET_M,
    SERVICE_DATES,
    detour_sections,
    line_detour,
    site_stop,
    static_feed,
)

WEEK = service_dates_from(SERVICE_DATES[0], 7)

METADATA = {"service_dates": SERVICE_DATES, "generated_at": "now", "attribution": "STM"}


def sample_result() -> BuildResult:
    cancelled_line, detoured_line = detour_sections(range(4, 7))
    stops = [
        site_stop(f"S{index}", (index - 1) * STOP_SPACING_M, cancelled=4 <= index <= 6)
        for index in range(1, 11)
    ]
    stops.append(site_stop("T1", 5.0 * STOP_SPACING_M, DETOUR_OFFSET_M, replacement=True))
    return build(
        [line_detour(stops, [(cancelled_line, detoured_line)])], static_feed(), SERVICE_DATES
    )


def test_the_json_counts_what_was_written():
    payload = build_json(sample_result(), METADATA)

    assert payload["entities"] == 1
    assert payload["modifications"] == 1
    assert payload["stops_defined"] == 1
    assert payload["detours"][0]["entity_id"] == "web_detour_51_1"
    assert payload["detours"][0]["modifications"][0]["cancelled_stop_ids"] == ["S4", "S5", "S6"]


def test_the_markdown_gives_the_run_of_dates_a_run_wrote():
    text = build_markdown(sample_result(), {**METADATA, "service_dates": WEEK})

    assert f"`{WEEK[0]}` to `{WEEK[-1]}`, 7 days" in text


def test_the_markdown_names_the_span_and_the_replacements():
    text = build_markdown(sample_result(), METADATA)

    assert "[4..6]" in text
    assert "`S4`" in text
    assert "serves `T1`" in text
    assert "STM" in text


def test_lines_that_produced_nothing_are_listed():
    result = BuildResult(skipped=[SkippedLine("51E", "no trip pattern running today is affected")])

    text = build_markdown(result, METADATA)

    assert "## Lines that produced nothing" in text
    assert "51E: no trip pattern running today is affected" in text


def test_a_read_is_due_once_the_clock_passes_the_next_turn(tmp_path):
    path = tmp_path / "metadata.json"
    path.write_text('{"generated_at": "2026-09-03T12:06:00+00:00"}')

    assert not is_due_file(path, datetime(2026, 9, 3, 12, 34, tzinfo=UTC))
    assert is_due_file(path, datetime(2026, 9, 3, 12, 35, tzinfo=UTC))


def test_a_read_delayed_into_the_next_turn_still_leaves_the_one_after_due():
    # The :05 run ran at :12; the :35 run is still a turn of its own.
    published = "2026-09-03T12:12:00+00:00"

    assert not is_due(published, datetime(2026, 9, 3, 12, 15, tzinfo=UTC))
    assert is_due(published, datetime(2026, 9, 3, 12, 38, tzinfo=UTC))


def test_the_turn_the_hour_turns_in_starts_at_thirty_five_past(tmp_path):
    path = tmp_path / "metadata.json"
    path.write_text('{"generated_at": "2026-09-03T12:36:00+00:00"}')

    assert not is_due_file(path, datetime(2026, 9, 3, 13, 4, tzinfo=UTC))
    assert is_due_file(path, datetime(2026, 9, 3, 13, 5, tzinfo=UTC))


def test_the_dates_a_run_writes_start_today_and_cross_the_month():
    dates = service_dates_from("20260928", 7)

    assert dates[0] == "20260928"
    assert dates[-1] == "20261004"
    assert len(dates) == 7
