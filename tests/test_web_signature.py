"""The fingerprint that decides whether the website map report is redrawn."""

from __future__ import annotations

import copy
import json

from tmweb.signature import detour_signature, detour_signature_of_file

REPORT = {
    "generated_at": "2026-09-03T13:13:18+00:00",
    "service_dates": ["20260903", "20260904"],
    "lines_read": 407,
    "detours": [
        {
            "entity_id": "web_detour_51_1",
            "route_id": "51",
            "trips": 38,
            "modifications": [
                {
                    "start_stop_sequence": 4,
                    "end_stop_sequence": 6,
                    "cancelled_stop_ids": ["S4", "S5", "S6"],
                    "replacement_stop_ids": ["T1"],
                    "kept_stop_id": None,
                }
            ],
        }
    ],
    "skipped_lines": [{"line": "97E", "reason": "no such route and direction in the GTFS"}],
}


def test_the_same_detours_give_the_same_fingerprint():
    assert detour_signature(REPORT) == detour_signature(copy.deepcopy(REPORT))


def test_another_run_of_the_same_detours_does_not_redraw():
    """A new run writes new dates and counts new trips; the maps are unchanged."""
    later = copy.deepcopy(REPORT)
    later["generated_at"] = "2026-09-03T14:13:04+00:00"
    later["service_dates"] = ["20260904", "20260905"]
    later["lines_read"] = 408
    later["detours"][0]["trips"] = 41

    assert detour_signature(later) == detour_signature(REPORT)


def test_a_longer_span_redraws():
    widened = copy.deepcopy(REPORT)
    widened["detours"][0]["modifications"][0]["end_stop_sequence"] = 7

    assert detour_signature(widened) != detour_signature(REPORT)


def test_another_replacement_stop_redraws():
    changed = copy.deepcopy(REPORT)
    changed["detours"][0]["modifications"][0]["replacement_stop_ids"] = ["T1", "T2"]

    assert detour_signature(changed) != detour_signature(REPORT)


def test_a_new_detour_redraws():
    added = copy.deepcopy(REPORT)
    added["detours"].append({"entity_id": "web_detour_10_2", "modifications": []})

    assert detour_signature(added) != detour_signature(REPORT)


def test_the_order_detours_arrive_in_does_not_matter():
    one = copy.deepcopy(REPORT)
    one["detours"].append({"entity_id": "web_detour_10_2", "modifications": []})

    other = copy.deepcopy(REPORT)
    other["detours"].insert(0, {"entity_id": "web_detour_10_2", "modifications": []})

    assert detour_signature(one) == detour_signature(other)


def test_an_empty_report_has_a_fingerprint():
    assert detour_signature({"detours": []})


def test_reading_a_report_from_disk(tmp_path):
    path = tmp_path / "report.json"
    path.write_text(json.dumps(REPORT), encoding="utf-8")

    assert detour_signature_of_file(path) == detour_signature(REPORT)
