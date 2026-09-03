"""Where the data comes from and how hard the repair pushes."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .repair import RepairConfig

STATIC_GTFS_URL = "https://www.stm.info/sites/default/files/gtfs/gtfs_stm.zip"
REALTIME_URL = "https://api.stm.info/pub/gtfs-rt/v2/api/gtfsrt/tripmodifications"

USERNAME_ENV = "STM_API_USERNAME"
PASSWORD_ENV = "STM_API_PASSWORD"

ATTRIBUTION = (
    "Source data: Societe de transport de Montreal (STM), "
    "licensed CC-BY 4.0. This is an unofficial derived feed."
)


@dataclass
class Settings:
    """Everything one run of the tool needs to know."""

    static_gtfs_url: str = STATIC_GTFS_URL
    realtime_url: str = REALTIME_URL
    cache_dir: Path = Path(".cache")
    output_dir: Path = Path("output")
    repair: RepairConfig = field(default_factory=RepairConfig)

    @property
    def credentials(self) -> tuple[str, str] | None:
        """The realtime feed's basic-auth pair, or None when it is not configured."""
        username = os.environ.get(USERNAME_ENV)
        password = os.environ.get(PASSWORD_ENV)
        return (username, password) if username and password else None
