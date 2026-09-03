"""Tests for polyline decoding and point-to-polyline distance."""

from __future__ import annotations

import math

import pytest

from fixtures import at, encode_polyline
from tmfix.geometry import LatLon, decode_polyline, distance_to_polyline


def test_decodes_the_example_from_the_polyline_spec():
    points = decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@")
    assert [(round(p.lat, 5), round(p.lon, 5)) for p in points] == [
        (38.5, -120.2),
        (40.7, -120.95),
        (43.252, -126.453),
    ]


def test_encode_and_decode_round_trip():
    original = [at(0), at(500, 200), at(1200, -300)]
    decoded = decode_polyline(encode_polyline(original))
    assert len(decoded) == len(original)
    for source, result in zip(original, decoded, strict=True):
        assert source.lat == pytest.approx(result.lat, abs=1e-5)
        assert source.lon == pytest.approx(result.lon, abs=1e-5)


def test_empty_polyline_decodes_to_nothing():
    assert decode_polyline("") == []


def test_truncated_polyline_is_rejected():
    truncated = encode_polyline([at(0), at(500)])[:-1]
    with pytest.raises(ValueError):
        decode_polyline(truncated)


def test_distance_to_a_point_on_the_line_is_zero():
    line = [at(0), at(1000)]
    assert distance_to_polyline(at(500), line) == pytest.approx(0.0, abs=0.5)


def test_distance_is_measured_perpendicular_to_the_segment():
    line = [at(0), at(1000)]
    assert distance_to_polyline(at(500, 300), line) == pytest.approx(300.0, rel=0.01)


def test_distance_past_the_end_is_measured_to_the_endpoint():
    line = [at(0), at(1000)]
    # 400 m beyond the end and 300 m to the side: a 3-4-5 triangle.
    assert distance_to_polyline(at(1400, 300), line) == pytest.approx(500.0, rel=0.01)


def test_a_one_point_line_measures_to_that_point():
    assert distance_to_polyline(at(300), [at(0)]) == pytest.approx(300.0, rel=0.01)


def test_empty_line_is_rejected():
    with pytest.raises(ValueError):
        distance_to_polyline(at(0), [])


def test_distance_matches_the_haversine_formula():
    start = LatLon(45.5, -73.6)
    end = LatLon(45.5, -73.5)
    midpoint = LatLon(45.51, -73.55)

    expected = 2 * 6_371_008.8 * math.asin(math.sqrt(math.sin(math.radians(0.01) / 2) ** 2))
    assert distance_to_polyline(midpoint, [start, end]) == pytest.approx(expected, rel=0.01)
