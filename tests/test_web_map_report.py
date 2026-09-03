"""The website feed's map report draws without asking a tile server for anything.

These are smoke tests: they prove the report package imports, that it can turn
a published feed back into pages, and that those pages reach a PDF. What the
maps look like is not something a test can judge.
"""

from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path

import pytest

from fixtures import STOP_SPACING_M
from tmweb.build import BuildResult, build
from tmweb.feed import build_feed
from web_fixtures import (
    DETOUR_OFFSET_M,
    SERVICE_DATES,
    add_pattern,
    detour_sections,
    line_detour,
    site_stop,
    static_feed,
)

pytest.importorskip("matplotlib", reason="the report extra is not installed")


def sample_result(feed=None, skipped: range = range(4, 7)) -> tuple[BuildResult, object]:
    """A line with one detour, built against a static feed."""
    cancelled_line, detoured_line = detour_sections(skipped)
    stops = [
        site_stop(f"S{index}", (index - 1) * STOP_SPACING_M, cancelled=index in skipped)
        for index in range(1, 11)
    ]
    stops.append(
        site_stop(f"T{skipped[0]}", skipped[1] * STOP_SPACING_M, DETOUR_OFFSET_M, replacement=True)
    )
    feed = feed or static_feed()
    detour = line_detour(stops, [(cancelled_line, detoured_line)])
    return build([detour], feed, SERVICE_DATES), feed


def write_zip(path: Path, static) -> Path:
    """The three tables the report reads names out of."""
    tables = {
        "stops.txt": [
            {"stop_id": stop_id, "stop_name": f"Stop {stop_id}"}
            for stop_id in static.stop_positions
        ],
        "routes.txt": [
            {"route_id": "51", "route_short_name": "51", "route_long_name": "Test line"}
        ],
        "trips.txt": [
            {"trip_id": trip_id, "route_id": "51", "trip_headsign": "Est", "shape_id": "51_1"}
            for trip_id in static.pattern_by_trip
        ],
    }

    with zipfile.ZipFile(path, "w") as archive:
        for name, rows in tables.items():
            buffer = io.StringIO()
            writer = csv.DictWriter(buffer, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
            archive.writestr(name, buffer.getvalue())
    return path


def collected(tmp_path: Path, result: BuildResult, static):
    from tmreport.web.collect import collect

    feed_path = tmp_path / "tripmodifications.pb"
    feed_path.write_bytes(
        build_feed(result, SERVICE_DATES, timestamp=1788436298).SerializeToString()
    )
    return collect(feed_path, static, write_zip(tmp_path / "static-gtfs.zip", static))


def test_a_detour_becomes_one_page(tmp_path):
    result, static = sample_result()

    report = collected(tmp_path, result, static)

    assert report.entities == 1
    assert report.service_dates == list(SERVICE_DATES)
    (page,) = report.pages
    assert page.route.short_name == "51"
    assert [stop_id for _, stop_id in page.skipped] == ["S4", "S5", "S6"]
    assert page.replacement_stop_ids == ["T4"]
    # The static feed has no such stop, so the feed defines it and the page says so.
    assert page.temporary_stop_ids == {"T4"}
    assert page.stop_names["S4"] == "Stop S4"


def test_patterns_with_the_same_detour_share_one_page(tmp_path):
    static = static_feed()
    add_pattern(static, "51_2", ["S3", "S4", "S5", "S6", "S7", "S8", "S9", "S10"])
    result, _ = sample_result(static, skipped=range(5, 8))

    report = collected(tmp_path, result, static)

    assert report.entities == 2
    (page,) = report.pages
    # The short turn runs fewer trips, so the page is the main pattern's.
    assert page.entity_id == "web_detour_51_1"
    assert page.also_patterns == ["web_detour_51_2"]


def test_a_pdf_is_written(tmp_path):
    from tmreport.web.render import build_report

    result, static = sample_result()
    report = collected(tmp_path, result, static)

    output = tmp_path / "report.pdf"
    written = build_report(report, output, use_basemap=False)

    assert written == len(report.pages) + 1  # the cover counts, and holds the contents
    assert output.stat().st_size > 1000
    assert output.read_bytes().startswith(b"%PDF")
