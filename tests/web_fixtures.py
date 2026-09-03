"""Synthetic website answers and static feeds for the tmweb tests.

The line runs east along one street. A detour leaves it, runs parallel well to
the north, and rejoins, which is the shape of every real STM detour.
"""

from __future__ import annotations

from fixtures import STOP_SPACING_M, at
from tmfix.geometry import LatLon
from tmfix.static_feed import Calendar, StaticFeed
from tmweb.site import Line, LineDetour, SiteRoute, SiteStop

SERVICE_DATE = "20260903"
SERVICE_ID = "weekday"
DETOUR_OFFSET_M = 800.0


def line(identifier: str = "51", direction: str = "E") -> Line:
    return Line(identifier=identifier, direction=direction, description="Test")


def static_feed(
    stop_count: int = 10,
    route_pattern_id: str = "51_1",
    route_id: str = "51",
    direction: str = "E",
    direction_id: str = "0",
    stops: list[str] | None = None,
    trips: int = 2,
) -> StaticFeed:
    """A feed with one route pattern of evenly spaced stops running east."""
    feed = StaticFeed()
    stop_ids = stops or [f"S{index + 1}" for index in range(stop_count)]

    for stop_id in stop_ids:
        feed.stop_positions[stop_id] = at(_east_of(stop_id))

    pattern = tuple((index + 1, stop_id) for index, stop_id in enumerate(stop_ids))
    feed.patterns.append(pattern)
    feed.pattern_by_route_pattern[route_pattern_id] = 0
    trip_ids = [f"{route_pattern_id}-trip-{index}" for index in range(trips)]
    for trip_id in trip_ids:
        feed.pattern_by_trip[trip_id] = 0
    feed.trips_by_route_pattern[route_pattern_id] = [(trip_id, SERVICE_ID) for trip_id in trip_ids]
    feed.route_patterns_by_direction[(route_id, direction_id)] = [route_pattern_id]
    feed.direction_ids[(route_id, direction)] = direction_id
    feed.shapes[route_pattern_id] = [
        at(index * STOP_SPACING_M) for index in range(len(stop_ids) + 1)
    ]
    feed.calendar = Calendar(weekly={SERVICE_ID: ("20260101", "20271231", (True,) * 7)})
    return feed


def add_pattern(
    feed: StaticFeed,
    route_pattern_id: str,
    stop_ids: list[str],
    route_id: str = "51",
    direction_id: str = "0",
    service_id: str = SERVICE_ID,
) -> None:
    """Add a second route pattern, for instance a short turn, to a feed."""
    pattern = tuple((index + 1, stop_id) for index, stop_id in enumerate(stop_ids))
    feed.patterns.append(pattern)
    index = len(feed.patterns) - 1
    feed.pattern_by_route_pattern[route_pattern_id] = index
    trip_id = f"{route_pattern_id}-trip-0"
    feed.pattern_by_trip[trip_id] = index
    feed.trips_by_route_pattern[route_pattern_id] = [(trip_id, service_id)]
    feed.route_patterns_by_direction.setdefault((route_id, direction_id), []).append(
        route_pattern_id
    )
    feed.shapes[route_pattern_id] = [feed.stop_positions[stop_id] for stop_id in stop_ids]


def _east_of(stop_id: str) -> float:
    """Stops are named S1, S2 and so on, evenly spaced from the base point."""
    return (int(stop_id[1:]) - 1) * STOP_SPACING_M


def detour_sections(skipped: range) -> tuple[list[LatLon], list[LatLon]]:
    """The road run a detour leaves, and the run it takes instead.

    Both start and end at the same two points, which is how the website
    publishes them.
    """
    leave = (skipped.start - 1.5) * STOP_SPACING_M
    rejoin = (skipped.stop - 0.5) * STOP_SPACING_M

    cancelled = [at(leave)]
    cancelled.extend(at((index - 1) * STOP_SPACING_M) for index in skipped)
    cancelled.append(at(rejoin))

    detoured = [
        at(leave),
        at(leave, DETOUR_OFFSET_M),
        at(rejoin, DETOUR_OFFSET_M),
        at(rejoin),
    ]
    return cancelled, detoured


def site_stop(
    stop_id: str,
    east_m: float,
    north_m: float = 0.0,
    cancelled: bool = False,
    replacement: bool = False,
) -> SiteStop:
    return SiteStop(
        stop_id=stop_id,
        name="Test stop",
        position=at(east_m, north_m),
        cancelled=cancelled,
        replacement=replacement,
    )


def line_detour(
    stops: list[SiteStop],
    sections: list[tuple[list[LatLon] | None, list[LatLon] | None]],
    stop_count: int = 10,
    identifier: str = "51",
    direction: str = "E",
) -> LineDetour:
    """One line's answer from the website."""
    route = SiteRoute(
        geometry=[at(index * STOP_SPACING_M) for index in range(stop_count + 1)],
        cancelled=[part for part, _ in sections if part],
        detoured=[part for _, part in sections if part],
    )
    return LineDetour(line=line(identifier, direction), stops=stops, route=route)
