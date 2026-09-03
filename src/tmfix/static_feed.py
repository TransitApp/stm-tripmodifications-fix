"""The parts of a static GTFS feed the repair needs.

Only two things are needed: where every stop is, and the ordered stop list of
every trip. Trips that visit the same stops in the same order share one stored
pattern, which keeps a city-sized feed small enough to cache between runs.
"""

from __future__ import annotations

import csv
import io
import pickle
import sys
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from .geometry import LatLon

# Bumped whenever the cached shape of StaticFeed changes, so an old cache
# file is ignored rather than unpickled into the wrong structure.
CACHE_VERSION = 1

# One stop of a trip: its stop_sequence and the stop it calls at.
PatternStop = tuple[int, str]
Pattern = tuple[PatternStop, ...]


@dataclass
class StaticFeed:
    """Stop positions and trip stop patterns from a static GTFS feed."""

    stop_positions: dict[str, LatLon] = field(default_factory=dict)
    patterns: list[Pattern] = field(default_factory=list)
    pattern_by_trip: dict[str, int] = field(default_factory=dict)

    def pattern_for_trip(self, trip_id: str) -> Pattern | None:
        """The ordered stops of a trip, or None if the feed does not have it."""
        index = self.pattern_by_trip.get(trip_id)
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


def load_from_zip(zip_path: Path) -> StaticFeed:
    """Read stops.txt and stop_times.txt out of a GTFS zip."""
    feed = StaticFeed()

    with zipfile.ZipFile(zip_path) as archive:
        for row in _read_rows(archive, "stops.txt"):
            lat = row.get("stop_lat")
            lon = row.get("stop_lon")
            if not lat or not lon:
                continue
            try:
                feed.stop_positions[row["stop_id"]] = LatLon(float(lat), float(lon))
            except ValueError:
                continue

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
