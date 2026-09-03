"""Polyline decoding and distance from a point to a polyline.

Distances are in metres. At the scale of a bus detour a local flat-earth
projection is accurate to well under a metre, so there is no need for
anything more elaborate.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

EARTH_RADIUS_M = 6_371_008.8
_METRES_PER_DEGREE_LAT = math.pi * EARTH_RADIUS_M / 180.0


@dataclass(frozen=True, slots=True)
class LatLon:
    """A WGS84 position in decimal degrees."""

    lat: float
    lon: float


def decode_polyline(encoded: str, precision: int = 5) -> list[LatLon]:
    """Decode a Google encoded polyline into positions.

    Raises ValueError if the string ends in the middle of a value.
    """
    factor = float(10**precision)
    points: list[LatLon] = []
    index = 0
    lat = 0
    lon = 0
    length = len(encoded)

    while index < length:
        deltas = []
        for _ in range(2):
            shift = 0
            result = 0
            while True:
                if index >= length:
                    raise ValueError("polyline ends mid-value")
                byte = ord(encoded[index]) - 63
                index += 1
                result |= (byte & 0x1F) << shift
                shift += 5
                if byte < 0x20:
                    break
            deltas.append(~(result >> 1) if result & 1 else result >> 1)
        lat += deltas[0]
        lon += deltas[1]
        points.append(LatLon(lat / factor, lon / factor))

    return points


def _project(point: LatLon, reference_lat: float) -> tuple[float, float]:
    """Project to local metres east/north, using one latitude for the whole set."""
    metres_per_degree_lon = _METRES_PER_DEGREE_LAT * math.cos(math.radians(reference_lat))
    return point.lon * metres_per_degree_lon, point.lat * _METRES_PER_DEGREE_LAT


def _distance_to_segment(
    point: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> float:
    """Shortest distance from a projected point to a projected segment."""
    px, py = point
    ax, ay = start
    dx = end[0] - ax
    dy = end[1] - ay

    if dx == 0.0 and dy == 0.0:
        return math.hypot(px - ax, py - ay)

    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def distance_to_polyline(point: LatLon, line: Sequence[LatLon]) -> float:
    """Shortest distance in metres from a point to a polyline.

    A one-point line is treated as that point. Raises ValueError on an empty line.
    """
    if not line:
        raise ValueError("cannot measure distance to an empty polyline")

    projected_point = _project(point, point.lat)
    projected_line = [_project(vertex, point.lat) for vertex in line]

    if len(projected_line) == 1:
        return math.hypot(
            projected_point[0] - projected_line[0][0],
            projected_point[1] - projected_line[0][1],
        )

    return min(
        _distance_to_segment(projected_point, projected_line[i], projected_line[i + 1])
        for i in range(len(projected_line) - 1)
    )
