"""The map report draws without asking a tile server for anything.

These are smoke tests: they prove the report package imports, that it can turn
a repair into pages, and that those pages reach a PDF. What the maps look like
is not something a test can judge.
"""

from __future__ import annotations

import pytest

from fixtures import (
    DETOUR_OFFSET_M,
    at,
    build_feed,
    detour_shape,
    straight_route,
    temporary_stop,
)
from tmfix.config import Settings
from tmfix.repair import repair_feed

pytest.importorskip("matplotlib", reason="the report extra is not installed")


def _repaired_pages():
    """A repair with one range extension, assembled into report pages."""
    from tmreport.collect import RouteLabel, build_pages

    stop_ids, static = straight_route(10)
    shape = detour_shape(10, range(0, 3))
    temporary = temporary_stop("T1", at(500, DETOUR_OFFSET_M))

    # The shape skips stops 1 to 3; the feed only cancels stop 1 and re-adds it.
    feed = build_feed(
        shape,
        [{"start": 1, "end": 1, "replacement_stops": ["T1", stop_ids[0]]}],
        extra_entities=[temporary],
    )
    result = repair_feed(feed, static, Settings().repair)

    stop_names = {stop_id: f"Stop {stop_id}" for stop_id in stop_ids}
    labels = {"trip-1": RouteLabel("99", "Test route", "Est")}
    return feed, result, build_pages(feed, result, static, stop_names, labels)


def test_a_repair_becomes_one_page():
    _, result, pages = _repaired_pages()

    assert result.repaired_entities, "the fixture should need a repair"
    assert len(pages) == 1

    page = pages[0]
    assert page.range_grew
    assert page.declared_range == (1, 1)
    assert page.repaired_range == (1, 3)
    assert page.route.short_name == "99"


def test_the_panels_differ_by_what_the_repair_changed():
    _, _, pages = _repaired_pages()
    page = pages[0]

    # The feed cancels one stop; the repair cancels the run the shape skips.
    assert len(page.before.cancelled_positions) == 1
    assert len(page.after.cancelled_positions) == 3

    # The re-added stop is a replacement before the repair and not after it.
    assert "S1" in page.before.replacement_stop_ids
    assert "S1" not in page.after.replacement_stop_ids


def test_a_pdf_is_written(tmp_path):
    from tmreport.collect import Collected
    from tmreport.render import build_report

    _, _, pages = _repaired_pages()
    collected = Collected(
        pages=pages,
        feed_timestamp=1788397504,
        threshold_m=100.0,
        entities_examined=1,
        entities_repaired=1,
    )

    output = tmp_path / "report.pdf"
    written = build_report(collected, output, use_basemap=False)

    assert written == len(pages) + 1  # the cover counts
    assert output.stat().st_size > 1000
    assert output.read_bytes().startswith(b"%PDF")
