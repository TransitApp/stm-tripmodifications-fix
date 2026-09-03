"""Tests for polyline decoding, projection onto a line and splicing."""

from __future__ import annotations

import math

import pytest

from fixtures import at
from tmfix.geometry import (
    LatLon,
    decode_polyline,
    distance_to_polyline,
    encode_polyline,
    point_along,
    project_onto,
    splice,
)


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


def test_projection_gives_the_distance_off_and_along():
    line = [at(0.0), at(1000.0), at(1000.0, 1000.0)]

    projection = project_onto(at(600.0, 40.0), line)

    assert projection.distance_m == pytest.approx(40.0, abs=0.5)
    assert projection.along_m == pytest.approx(600.0, abs=0.5)


def test_projection_past_the_end_lands_on_the_end():
    line = [at(0.0), at(1000.0)]

    projection = project_onto(at(1500.0), line)

    assert projection.along_m == pytest.approx(1000.0, abs=0.5)
    assert projection.distance_m == pytest.approx(500.0, abs=0.5)


def test_a_point_along_a_line_is_where_it_should_be():
    line = [at(0.0), at(1000.0), at(1000.0, 1000.0)]

    assert point_along(line, 1500.0).lat == pytest.approx(at(1000.0, 500.0).lat, abs=1e-6)
    assert point_along(line, -10.0) == line[0]
    assert point_along(line, 99_999.0) == line[-1]


def test_splicing_swaps_a_run_of_the_line_for_another():
    base = [at(0.0), at(2000.0)]
    detour = [at(500.0, 300.0), at(1500.0, 300.0)]

    spliced = splice(base, [(500.0, 1500.0, detour)])

    assert spliced[0].lon == pytest.approx(base[0].lon)
    assert spliced[-1].lon == pytest.approx(base[-1].lon)
    assert max(point.lat for point in spliced) == pytest.approx(detour[0].lat)


def test_a_polyline_survives_a_round_trip():
    line = [at(0.0), at(1000.0), at(1000.0, 1000.0)]

    decoded = decode_polyline(encode_polyline(line))

    for original, result in zip(line, decoded, strict=True):
        assert result.lat == pytest.approx(original.lat, abs=1e-5)
        assert result.lon == pytest.approx(original.lon, abs=1e-5)
