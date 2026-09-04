"""Where the data comes from, and which service day a run describes."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from tmfix.config import STATIC_GTFS_URL

from .build import BuildConfig
from .site import BASE_URL, REQUESTS_PER_SECOND, WORKERS

AGENCY_TIMEZONE = ZoneInfo("America/Montreal")

# How many service dates a run writes, counting today. The website says nothing
# about how long a detour lasts, so this is an assumption: a detour published
# now is taken to hold for today and the fortnight after it.
DEFAULT_DAYS = 15

ATTRIBUTION = (
    "Source data: Societe de transport de Montreal (STM), "
    "licensed CC-BY 4.0. This is an unofficial derived feed."
)


def service_date_now(moment: datetime | None = None) -> str:
    """Today's date in Montreal, as GTFS writes it."""
    moment = moment or datetime.now(AGENCY_TIMEZONE)
    return moment.astimezone(AGENCY_TIMEZONE).strftime("%Y%m%d")


def service_dates_from(start: str, days: int) -> Sequence[str]:
    """The run of YYYYMMDD dates a run writes, starting at `start`."""
    first = datetime.strptime(start, "%Y%m%d")
    return [(first + timedelta(days=day)).strftime("%Y%m%d") for day in range(days)]


@dataclass
class Settings:
    """Everything one run of the tool needs to know."""

    static_gtfs_url: str = STATIC_GTFS_URL
    site_url: str = BASE_URL
    cache_dir: Path = Path(".cache")
    output_dir: Path = Path("output-web")
    workers: int = WORKERS
    requests_per_second: float = REQUESTS_PER_SECOND
    days: int = DEFAULT_DAYS
    build: BuildConfig = field(default_factory=BuildConfig)
