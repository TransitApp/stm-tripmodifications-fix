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


def encode_polyline(points: Sequence[LatLon], precision: int = 5) -> str:
    """Encode positions as a Google encoded polyline."""
    factor = 10**precision
    output: list[str] = []
    previous = (0, 0)

    for point in points:
        current = (round(point.lat * factor), round(point.lon * factor))
        for value, last in zip(current, previous, strict=True):
            delta = value - last
            delta = ~(delta << 1) if delta < 0 else (delta << 1)
            while delta >= 0x20:
                output.append(chr((0x20 | (delta & 0x1F)) + 63))
                delta >>= 5
            output.append(chr(delta + 63))
        previous = current

    return "".join(output)


def cumulative_lengths(line: Sequence[LatLon]) -> list[float]:
    """The distance in metres from the start of the line to each of its vertices."""
    lengths = [0.0]
    for index in range(len(line) - 1):
        lengths.append(lengths[-1] + distance_between(line[index], line[index + 1]))
    return lengths


def distance_between(first: LatLon, second: LatLon) -> float:
    """Distance in metres between two positions."""
    reference = (first.lat + second.lat) / 2.0
    metres_per_degree_lon = _METRES_PER_DEGREE_LAT * math.cos(math.radians(reference))
    return math.hypot(
        (first.lon - second.lon) * metres_per_degree_lon,
        (first.lat - second.lat) * _METRES_PER_DEGREE_LAT,
    )


@dataclass(frozen=True, slots=True)
class Projection:
    """Where a point falls on a polyline."""

    distance_m: float
    along_m: float


def project_onto(
    point: LatLon,
    line: Sequence[LatLon],
    lengths: Sequence[float] | None = None,
) -> Projection:
    """The nearest point on a polyline: how far off it is, and how far along.

    Raises ValueError on an empty line.
    """
    if not line:
        raise ValueError("cannot project onto an empty polyline")
    if len(line) == 1:
        return Projection(distance_between(point, line[0]), 0.0)

    lengths = list(lengths) if lengths is not None else cumulative_lengths(line)
    scale = math.cos(math.radians(point.lat))
    px, py = point.lon * scale, point.lat
    best = Projection(math.inf, 0.0)

    for index in range(len(line) - 1):
        start, end = line[index], line[index + 1]
        ax, ay = start.lon * scale, start.lat
        dx, dy = end.lon * scale - ax, end.lat - ay
        if dx == 0.0 and dy == 0.0:
            fraction = 0.0
        else:
            fraction = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
            fraction = max(0.0, min(1.0, fraction))
        nearest = LatLon(
            start.lat + fraction * (end.lat - start.lat),
            start.lon + fraction * (end.lon - start.lon),
        )
        distance = distance_between(point, nearest)
        if distance < best.distance_m:
            span = lengths[index + 1] - lengths[index]
            best = Projection(distance, lengths[index] + fraction * span)

    return best


def point_along(
    line: Sequence[LatLon],
    distance_m: float,
    lengths: Sequence[float] | None = None,
) -> LatLon:
    """The position the given distance along a polyline, clamped to its ends."""
    lengths = list(lengths) if lengths is not None else cumulative_lengths(line)
    if distance_m <= 0.0:
        return line[0]
    if distance_m >= lengths[-1]:
        return line[-1]

    for index in range(len(line) - 1):
        if lengths[index + 1] < distance_m:
            continue
        span = lengths[index + 1] - lengths[index]
        fraction = 0.0 if span == 0.0 else (distance_m - lengths[index]) / span
        start, end = line[index], line[index + 1]
        return LatLon(
            start.lat + fraction * (end.lat - start.lat),
            start.lon + fraction * (end.lon - start.lon),
        )
    return line[-1]


def splice(
    base: Sequence[LatLon],
    replacements: Sequence[tuple[float, float, Sequence[LatLon]]],
) -> list[LatLon]:
    """Rebuild a polyline with runs of it swapped out for other polylines.

    Each replacement gives the distances along `base` it covers and the line to
    put there. Overlapping replacements are not allowed; they are applied in
    order along the base.
    """
    lengths = cumulative_lengths(base)
    result: list[LatLon] = []
    cursor = 0.0

    for start, end, detour in sorted(replacements, key=lambda item: (item[0], item[1])):
        result.extend(_run(base, lengths, cursor, start))
        result.extend(detour)
        cursor = max(cursor, end)

    result.extend(_run(base, lengths, cursor, lengths[-1]))
    return result


def _run(
    line: Sequence[LatLon],
    lengths: Sequence[float],
    start: float,
    end: float,
) -> list[LatLon]:
    """The part of a polyline between two distances along it."""
    if end <= start:
        return []
    run = [point_along(line, start, lengths)]
    run.extend(line[index] for index in range(len(line)) if start < lengths[index] < end)
    run.append(point_along(line, end, lengths))
    return run
