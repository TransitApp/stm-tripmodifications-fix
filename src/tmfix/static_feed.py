"""The parts of a static GTFS feed the two tools need.

The repair needs two things: where every stop is, and the ordered stop list of
every trip. Trips that visit the same stops in the same order share one stored
pattern, which keeps a city-sized feed small enough to cache between runs.

Building modifications from the STM website needs more: which route pattern a
trip belongs to, which shape that pattern runs on, and which trips run on a
given date. All of it is read in the same pass, because stop_times.txt is
200 MB and reading it twice would dominate a run.
"""

from __future__ import annotations

import csv
import io
import pickle
import sys
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .geometry import LatLon

# Bumped whenever the cached shape of StaticFeed changes, so an old cache
# file is ignored rather than unpickled into the wrong structure.
CACHE_VERSION = 2

# One stop of a trip: its stop_sequence and the stop it calls at.
PatternStop = tuple[int, str]
Pattern = tuple[PatternStop, ...]

_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


@dataclass
class Calendar:
    """Which service IDs run on a given date."""

    weekly: dict[str, tuple[str, str, tuple[bool, ...]]] = field(default_factory=dict)
    added: dict[str, set[str]] = field(default_factory=dict)
    removed: dict[str, set[str]] = field(default_factory=dict)

    def services_on(self, day: str) -> set[str]:
        """The service IDs running on a YYYYMMDD date."""
        weekday = date(int(day[:4]), int(day[4:6]), int(day[6:8])).weekday()
        running = {
            service_id
            for service_id, (start, end, days) in self.weekly.items()
            if start <= day <= end and days[weekday]
        }
        running |= self.added.get(day, set())
        running -= self.removed.get(day, set())
        return running


@dataclass
class StaticFeed:
    """Stop positions, trip stop patterns and route patterns from a static GTFS feed."""

    stop_positions: dict[str, LatLon] = field(default_factory=dict)
    patterns: list[Pattern] = field(default_factory=list)
    pattern_by_trip: dict[str, int] = field(default_factory=dict)
    # route_pattern_id -> the index in `patterns` its trips share
    pattern_by_route_pattern: dict[str, int] = field(default_factory=dict)
    # (route_id, direction_id) -> the route patterns it is made of
    route_patterns_by_direction: dict[tuple[str, str], list[str]] = field(default_factory=dict)
    # route_pattern_id -> (trip_id, service_id) for every trip on it
    trips_by_route_pattern: dict[str, list[tuple[str, str]]] = field(default_factory=dict)
    # (route_id, "W") -> direction_id, from directions.txt
    direction_ids: dict[tuple[str, str], str] = field(default_factory=dict)
    shapes: dict[str, list[LatLon]] = field(default_factory=dict)
    calendar: Calendar = field(default_factory=Calendar)

    def pattern_for_trip(self, trip_id: str) -> Pattern | None:
        """The ordered stops of a trip, or None if the feed does not have it."""
        index = self.pattern_by_trip.get(trip_id)
        return None if index is None else self.patterns[index]

    def pattern_for_route_pattern(self, route_pattern_id: str) -> Pattern | None:
        """The ordered stops every trip on a route pattern calls at."""
        index = self.pattern_by_route_pattern.get(route_pattern_id)
        return None if index is None else self.patterns[index]

    def position(self, stop_id: str) -> LatLon | None:
        """Where a stop is, or None if the feed does not have it."""
        return self.stop_positions.get(stop_id)

    @property
    def trip_count(self) -> int:
        return len(self.pattern_by_trip)


def _read_rows(archive: zipfile.ZipFile, name: str) -> Iterator[dict[str, str]]:
    """Stream one CSV table out of a GTFS zip."""
    with archive.open(name) as raw:
        text = io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
        yield from csv.DictReader(text)


def _load_stops(archive: zipfile.ZipFile, feed: StaticFeed) -> None:
    for row in _read_rows(archive, "stops.txt"):
        lat = row.get("stop_lat")
        lon = row.get("stop_lon")
        if not lat or not lon:
            continue
        try:
            feed.stop_positions[row["stop_id"]] = LatLon(float(lat), float(lon))
        except ValueError:
            continue


def _load_calendar(archive: zipfile.ZipFile, feed: StaticFeed) -> None:
    for row in _read_rows(archive, "calendar.txt"):
        days = tuple(row.get(name) == "1" for name in _WEEKDAYS)
        feed.calendar.weekly[row["service_id"]] = (row["start_date"], row["end_date"], days)

    for row in _read_rows(archive, "calendar_dates.txt"):
        target = feed.calendar.added if row["exception_type"] == "1" else feed.calendar.removed
        target.setdefault(row["date"], set()).add(row["service_id"])


def _load_shapes(archive: zipfile.ZipFile, feed: StaticFeed) -> None:
    """Read shapes.txt, keyed by route pattern rather than by shape.

    The STM gives every route pattern one shape whose ID is the pattern's own,
    so nothing is lost by keying on the pattern.
    """
    points: dict[str, list[tuple[int, LatLon]]] = {}
    for row in _read_rows(archive, "shapes.txt"):
        key = row.get("route_pattern_id") or row.get("shape_id", "")
        if not key:
            continue
        try:
            sequence = int(row["shape_pt_sequence"])
            position = LatLon(float(row["shape_pt_lat"]), float(row["shape_pt_lon"]))
        except (KeyError, ValueError):
            continue
        points.setdefault(key, []).append((sequence, position))

    for key, values in points.items():
        values.sort()
        feed.shapes[key] = [position for _, position in values]


def _load_directions(archive: zipfile.ZipFile, feed: StaticFeed) -> None:
    if "directions.txt" not in archive.namelist():
        return
    for row in _read_rows(archive, "directions.txt"):
        legacy = row.get("direction_legacy", "")
        if legacy:
            feed.direction_ids[(row["route_id"], legacy[0].upper())] = row["direction_id"]


def _load_trips(archive: zipfile.ZipFile, feed: StaticFeed) -> dict[str, str]:
    """Read trips.txt. Returns the route pattern of every trip."""
    pattern_of_trip: dict[str, str] = {}
    for row in _read_rows(archive, "trips.txt"):
        route_pattern_id = row.get("route_pattern_id") or row.get("shape_id") or ""
        if not route_pattern_id:
            continue
        trip_id = row["trip_id"]
        pattern_of_trip[trip_id] = route_pattern_id
        feed.trips_by_route_pattern.setdefault(route_pattern_id, []).append(
            (trip_id, row["service_id"])
        )
        direction = (row["route_id"], row.get("direction_id", ""))
        known = feed.route_patterns_by_direction.setdefault(direction, [])
        if route_pattern_id not in known:
            known.append(route_pattern_id)
    return pattern_of_trip


def load_from_zip(zip_path: Path) -> StaticFeed:
    """Read everything the two tools need out of a GTFS zip."""
    feed = StaticFeed()

    with zipfile.ZipFile(zip_path) as archive:
        _load_stops(archive, feed)
        _load_calendar(archive, feed)
        _load_shapes(archive, feed)
        _load_directions(archive, feed)
        pattern_of_trip = _load_trips(archive, feed)

        # Interning stop IDs matters here: a large feed repeats each one
        # thousands of times across stop_times.txt.
        stops_by_trip: dict[str, list[PatternStop]] = {}
        for row in _read_rows(archive, "stop_times.txt"):
            try:
                sequence = int(row["stop_sequence"])
            except (KeyError, ValueError):
                continue
            trip_id = row["trip_id"]
            stops_by_trip.setdefault(trip_id, []).append((sequence, sys.intern(row["stop_id"])))

    pattern_index: dict[Pattern, int] = {}
    for trip_id, stops in stops_by_trip.items():
        stops.sort()
        pattern = tuple(stops)
        index = pattern_index.get(pattern)
        if index is None:
            index = len(feed.patterns)
            pattern_index[pattern] = index
            feed.patterns.append(pattern)
        feed.pattern_by_trip[trip_id] = index
        route_pattern_id = pattern_of_trip.get(trip_id)
        if route_pattern_id is not None:
            feed.pattern_by_route_pattern.setdefault(route_pattern_id, index)

    return feed


def save_cache(feed: StaticFeed, path: Path) -> None:
    """Write a parsed feed so the next run can skip parsing."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        payload = {"version": CACHE_VERSION, "feed": feed}
        pickle.dump(payload, handle, protocol=pickle.HIGHEST_PROTOCOL)


def load_cache(path: Path) -> StaticFeed | None:
    """Read a parsed feed back, or None if it is missing or from an older version."""
    if not path.exists():
        return None
    try:
        with path.open("rb") as handle:
            payload = pickle.load(handle)
    except Exception:
        return None
    if not isinstance(payload, dict) or payload.get("version") != CACHE_VERSION:
        return None
    feed = payload.get("feed")
    return feed if isinstance(feed, StaticFeed) else None
