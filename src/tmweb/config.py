"""Where the data comes from, and which service day a run describes."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from tmfix.config import STATIC_GTFS_URL

from .build import BuildConfig
from .site import BASE_URL

AGENCY_TIMEZONE = ZoneInfo("America/Montreal")

ATTRIBUTION = (
    "Source data: Societe de transport de Montreal (STM), "
    "licensed CC-BY 4.0. This is an unofficial derived feed."
)


def service_date_now(moment: datetime | None = None) -> str:
    """Today's date in Montreal, as GTFS writes it."""
    moment = moment or datetime.now(AGENCY_TIMEZONE)
    return moment.astimezone(AGENCY_TIMEZONE).strftime("%Y%m%d")


@dataclass
class Settings:
    """Everything one run of the tool needs to know."""

    static_gtfs_url: str = STATIC_GTFS_URL
    site_url: str = BASE_URL
    cache_dir: Path = Path(".cache")
    output_dir: Path = Path("output-web")
    workers: int = 8
    build: BuildConfig = field(default_factory=BuildConfig)
