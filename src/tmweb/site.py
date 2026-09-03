"""Reading detours out of the STM website's own API.

This is the API behind the line pages on stm.info. It is not the developer
feed and needs no key, but it does refuse any request without an `Origin`
header naming the site.

Three things come out of it. `lines` lists every line and direction. For one
line and direction, `stops` gives the scheduled stop list with two flags on
it — `cxl` for a stop the detour skips and `dtr` for one it serves instead —
and `routes/default` gives the scheduled shape along with the road sections
the detour leaves and the ones it takes.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

import requests

from tmfix.geometry import LatLon

logger = logging.getLogger(__name__)

BASE_URL = "https://api.stm.info/pub/i3/v1c/api"
ORIGIN = "https://www.stm.info"
LANGUAGE = "fr"

TIMEOUT_S = 30
ATTEMPTS = 3

# The website names directions in full; the GTFS calls them by their initial.
DIRECTION_LETTERS = {"North": "N", "South": "S", "East": "E", "West": "W"}


@dataclass(frozen=True, slots=True)
class Line:
    """One line in one direction, as the website names it."""

    identifier: str
    direction: str
    description: str

    @property
    def key(self) -> str:
        return f"{self.identifier}{self.direction}"


@dataclass(frozen=True, slots=True)
class SiteStop:
    """A stop on a line, and what the detour does to it."""

    stop_id: str
    name: str
    position: LatLon
    cancelled: bool
    replacement: bool


@dataclass
class SiteRoute:
    """The shape of a line in one direction, and its detours."""

    geometry: list[LatLon] = field(default_factory=list)
    cancelled: list[list[LatLon]] = field(default_factory=list)
    detoured: list[list[LatLon]] = field(default_factory=list)


@dataclass
class LineDetour:
    """Everything the website says about one line and direction."""

    line: Line
    stops: list[SiteStop]
    route: SiteRoute

    @property
    def cancelled_stop_ids(self) -> set[str]:
        return {stop.stop_id for stop in self.stops if stop.cancelled}

    @property
    def replacement_stops(self) -> list[SiteStop]:
        return [stop for stop in self.stops if stop.replacement]

    @property
    def has_detour(self) -> bool:
        return bool(self.cancelled_stop_ids or self.replacement_stops)


class Site:
    """A session against the STM website API."""

    def __init__(self, base_url: str = BASE_URL, language: str = LANGUAGE, workers: int = 8):
        self.base_url = base_url
        self.language = language
        self.workers = workers
        self.session = requests.Session()
        self.session.headers.update({"Origin": ORIGIN, "Accept": "application/json"})

    def _get(self, path: str, params: dict[str, str]) -> Any:
        url = f"{self.base_url}/{self.language}/{path}"
        last: Exception | None = None
        for attempt in range(ATTEMPTS):
            try:
                response = self.session.get(url, params=params, timeout=TIMEOUT_S)
                response.raise_for_status()
                return response.json()
            # Any failure is worth one more try.
            except Exception as error:
                last = error
                logger.debug("attempt %d for %s failed: %s", attempt + 1, url, error)
        raise RuntimeError(f"could not read {url}") from last

    def lines(self) -> list[Line]:
        """Every line and direction the website knows."""
        payload = self._get("lines", {"o": "web"})
        found = []
        for row in payload:
            letter = DIRECTION_LETTERS.get(row.get("direction", ""))
            if letter is None:
                continue
            found.append(
                Line(
                    identifier=str(row["public_identifier"]),
                    direction=letter,
                    description=row.get("description", ""),
                )
            )
        return found

    def stops(self, line: Line) -> list[SiteStop]:
        """The stop list of a line, with the detour flags on it."""
        payload = self._get(
            f"lines/{line.identifier}/stops",
            {
                "direction": line.direction,
                "withconnection": "0",
                "detoured": "1",
                "canceled": "1",
                "o": "web",
            },
        )
        return [_stop(row) for row in payload.get("result", []) if _has_position(row)]

    def route(self, line: Line) -> SiteRoute:
        """The shape of a line, with the sections its detours leave and take."""
        payload = self._get(
            f"lines/{line.identifier}/routes/default",
            {"direction": line.direction, "detoured": "1", "canceled": "1", "o": "web"},
        )
        return SiteRoute(
            geometry=_points(payload.get("Geometry", [])),
            cancelled=[_points(part) for part in payload.get("canceled", [])],
            detoured=[_points(part) for part in payload.get("detoured", [])],
        )

    def detours(self, lines: Sequence[Line]) -> list[LineDetour]:
        """Read every line, and the route of each one that has a detour.

        The stop list settles whether a line is detoured at all, so the route —
        which is the larger of the two responses — is only fetched for the few
        lines that are.
        """
        stops_by_line = dict(self._map(self.stops, lines))
        detoured = [line for line in lines if _flagged(stops_by_line.get(line, []))]
        logger.info("%d of %d line directions are detoured", len(detoured), len(lines))
        routes = dict(self._map(self.route, detoured))
        return [
            LineDetour(line=line, stops=stops_by_line[line], route=routes[line])
            for line in detoured
        ]

    def _map(self, work: Any, lines: Sequence[Line]) -> Iterable[tuple[Line, Any]]:
        if not lines:
            return []
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            return list(zip(lines, pool.map(work, lines), strict=True))


def _flagged(stops: Sequence[SiteStop]) -> bool:
    return any(stop.cancelled or stop.replacement for stop in stops)


def _has_position(row: dict[str, Any]) -> bool:
    return bool(row.get("lat")) and bool(row.get("lon"))


def _stop(row: dict[str, Any]) -> SiteStop:
    return SiteStop(
        stop_id=str(row["identifier"]),
        name=row.get("description", ""),
        position=LatLon(float(row["lat"]), float(row["lon"])),
        cancelled=bool(row.get("cxl")),
        replacement=bool(row.get("dtr")),
    )


def _points(part: Sequence[dict[str, Any]]) -> list[LatLon]:
    return [LatLon(float(point["lat"]), float(point["lon"])) for point in part]
