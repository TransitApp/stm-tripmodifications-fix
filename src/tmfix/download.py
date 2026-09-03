"""Fetching the two inputs.

The static feed is tens of megabytes and changes a few times a year, so it is
fetched conditionally and only re-read when it actually changed. The realtime
feed is small and changes constantly, so it is fetched every run.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import requests

USER_AGENT = "stm-tripmodifications-fix (+https://github.com/TransitApp/stm-tripmodifications-fix)"
TIMEOUT_S = 120


@dataclass
class StaticDownload:
    """Where the static feed landed and whether it is new."""

    path: Path
    version: str
    changed: bool


def _validators_path(cache_dir: Path) -> Path:
    return cache_dir / "static-validators.json"


def _read_validators(cache_dir: Path) -> dict[str, str]:
    path = _validators_path(cache_dir)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def fetch_static_gtfs(url: str, cache_dir: Path) -> StaticDownload:
    """Download the static GTFS zip unless the server says it has not changed.

    The returned version string is the server's ETag when it gives one, falling
    back to Last-Modified and then to the file's size. It is what the parsed
    feed cache is keyed on.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    zip_path = cache_dir / "static-gtfs.zip"
    validators = _read_validators(cache_dir)

    headers = {"User-Agent": USER_AGENT}
    if zip_path.exists():
        if etag := validators.get("etag"):
            headers["If-None-Match"] = etag
        if last_modified := validators.get("last_modified"):
            headers["If-Modified-Since"] = last_modified

    response = requests.get(url, headers=headers, timeout=TIMEOUT_S, stream=True)

    if response.status_code == 304 and zip_path.exists():
        response.close()
        return StaticDownload(
            path=zip_path,
            version=validators.get("version", str(zip_path.stat().st_size)),
            changed=False,
        )

    response.raise_for_status()
    temporary = zip_path.with_suffix(".zip.part")
    with temporary.open("wb") as handle:
        for chunk in response.iter_content(chunk_size=1 << 16):
            handle.write(chunk)
    temporary.replace(zip_path)

    etag = response.headers.get("ETag", "")
    last_modified = response.headers.get("Last-Modified", "")
    version = etag or last_modified or str(zip_path.stat().st_size)
    _validators_path(cache_dir).write_text(
        json.dumps({"etag": etag, "last_modified": last_modified, "version": version})
    )

    return StaticDownload(path=zip_path, version=version, changed=True)


def fetch_realtime(url: str, credentials: tuple[str, str] | None) -> bytes:
    """Download the TripModifications feed."""
    response = requests.get(
        url,
        headers={"User-Agent": USER_AGENT},
        auth=credentials,
        timeout=TIMEOUT_S,
    )
    response.raise_for_status()
    return response.content
