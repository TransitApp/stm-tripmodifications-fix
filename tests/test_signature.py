"""The fingerprint that decides whether the PDF map report is redrawn."""

from __future__ import annotations

import copy
import json

from tmfix.signature import repair_signature, repair_signature_of_file

REPORT = {
    "generated_at": "2026-09-03T01:32:47+00:00",
    "feed_timestamp": 1788399084,
    "entities_examined": 170,
    "repaired": [
        {
            "entity_id": "detour_66_N_13_60625",
            "shape_id": "66_N_13_60625",
            "sample_trip_id": "301162953",
            "modifications": [
                {
                    "index": 0,
                    "declared_range": [1, 1],
                    "repaired_range": [1, 3],
                    "stops_added_to_cancellation": [
                        {"stop_sequence": 2, "stop_id": "52330", "distance_to_shape_m": 356.1},
                        {"stop_sequence": 3, "stop_id": "61832", "distance_to_shape_m": 147.4},
                    ],
                    "replacement_stops_dropped": [
                        {"stop_id": "52285", "distance_to_shape_m": 347.2},
                    ],
                }
            ],
        }
    ],
    "passed_through": [{"entity_id": "detour_711_W_78_61943", "modifications": []}],
}


def test_the_same_repairs_give_the_same_fingerprint():
    assert repair_signature(REPORT) == repair_signature(copy.deepcopy(REPORT))


def test_a_new_run_of_the_same_feed_does_not_redraw():
    """Only the timestamps and the sampled trip moved, so the maps are unchanged."""
    later = copy.deepcopy(REPORT)
    later["generated_at"] = "2026-09-03T02:12:00+00:00"
    later["feed_timestamp"] = 1788401484
    later["entities_examined"] = 171
    later["repaired"][0]["sample_trip_id"] = "301162999"

    assert repair_signature(later) == repair_signature(REPORT)


def test_a_distance_that_barely_moves_does_not_redraw():
    """Distances are printed but never decide a page; jitter should not redraw."""
    jittered = copy.deepcopy(REPORT)
    modification = jittered["repaired"][0]["modifications"][0]
    modification["stops_added_to_cancellation"][0]["distance_to_shape_m"] = 356.4

    assert repair_signature(jittered) == repair_signature(REPORT)


def test_a_wider_range_redraws():
    widened = copy.deepcopy(REPORT)
    widened["repaired"][0]["modifications"][0]["repaired_range"] = [1, 4]

    assert repair_signature(widened) != repair_signature(REPORT)


def test_a_different_dropped_stop_redraws():
    changed = copy.deepcopy(REPORT)
    changed["repaired"][0]["modifications"][0]["replacement_stops_dropped"] = [
        {"stop_id": "52286", "distance_to_shape_m": 347.2}
    ]

    assert repair_signature(changed) != repair_signature(REPORT)


def test_a_new_repaired_entity_redraws():
    added = copy.deepcopy(REPORT)
    added["repaired"].append(
        {
            "entity_id": "detour_10_N_2_69807",
            "shape_id": "10_N_2_69807",
            "modifications": [
                {
                    "index": 0,
                    "declared_range": [1, 1],
                    "repaired_range": [1, 3],
                    "stops_added_to_cancellation": [],
                    "replacement_stops_dropped": [],
                }
            ],
        }
    )

    assert repair_signature(added) != repair_signature(REPORT)


def test_the_order_entities_arrive_in_does_not_matter():
    one = copy.deepcopy(REPORT)
    one["repaired"].append({"entity_id": "detour_10_N_2_69807", "modifications": []})

    other = copy.deepcopy(REPORT)
    other["repaired"].insert(0, {"entity_id": "detour_10_N_2_69807", "modifications": []})

    assert repair_signature(one) == repair_signature(other)


def test_an_empty_report_has_a_fingerprint():
    assert repair_signature({"repaired": []})


def test_reading_a_report_from_disk(tmp_path):
    path = tmp_path / "report.json"
    path.write_text(json.dumps(REPORT), encoding="utf-8")

    assert repair_signature_of_file(path) == repair_signature(REPORT)
