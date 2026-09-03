"""What the report says about a run."""

from __future__ import annotations

from datetime import UTC, datetime

from fixtures import STOP_SPACING_M
from tmweb.age import minutes_since_file
from tmweb.build import BuildResult, SkippedLine, build
from tmweb.report import build_json, build_markdown
from web_fixtures import (
    DETOUR_OFFSET_M,
    SERVICE_DATE,
    detour_sections,
    line_detour,
    site_stop,
    static_feed,
)

METADATA = {"service_date": SERVICE_DATE, "generated_at": "now", "attribution": "STM"}


def sample_result() -> BuildResult:
    cancelled_line, detoured_line = detour_sections(range(4, 7))
    stops = [
        site_stop(f"S{index}", (index - 1) * STOP_SPACING_M, cancelled=4 <= index <= 6)
        for index in range(1, 11)
    ]
    stops.append(site_stop("T1", 5.0 * STOP_SPACING_M, DETOUR_OFFSET_M, replacement=True))
    return build(
        [line_detour(stops, [(cancelled_line, detoured_line)])], static_feed(), SERVICE_DATE
    )


def test_the_json_counts_what_was_written():
    payload = build_json(sample_result(), METADATA)

    assert payload["entities"] == 1
    assert payload["modifications"] == 1
    assert payload["stops_defined"] == 1
    assert payload["detours"][0]["entity_id"] == "web_detour_51_1"
    assert payload["detours"][0]["modifications"][0]["cancelled_stop_ids"] == ["S4", "S5", "S6"]


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


def test_the_age_of_a_published_run_is_read_from_its_own_timestamp(tmp_path):
    path = tmp_path / "metadata.json"
    path.write_text('{"generated_at": "2026-09-03T12:00:00+00:00"}')

    now = datetime(2026, 9, 3, 13, 5, tzinfo=UTC)

    assert minutes_since_file(path, now) == 65
