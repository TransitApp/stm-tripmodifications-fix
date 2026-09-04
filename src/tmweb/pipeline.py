"""One run: read the website, build the feed, write the artifacts."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from google.protobuf.json_format import MessageToDict

from tmfix.config import Settings as FixSettings
from tmfix.pipeline import load_static_feed

from . import report
from .build import BuildResult, build
from .config import ATTRIBUTION, Settings, service_date_now, service_dates_from
from .feed import build_feed
from .site import Site

logger = logging.getLogger(__name__)


def write_artifacts(
    result: BuildResult,
    feed_bytes: bytes,
    feed_json: dict[str, object],
    metadata: dict[str, object],
    settings: Settings,
) -> dict[str, object]:
    """Write the feed, its JSON form and the report."""
    settings.output_dir.mkdir(parents=True, exist_ok=True)

    (settings.output_dir / "tripmodifications.pb").write_bytes(feed_bytes)
    (settings.output_dir / "tripmodifications.json").write_text(
        json.dumps(feed_json, indent=1, ensure_ascii=False)
    )

    report_json = report.build_json(result, metadata)
    (settings.output_dir / "report.json").write_text(
        json.dumps(report_json, indent=1, ensure_ascii=False)
    )
    (settings.output_dir / "report.md").write_text(report.build_markdown(result, metadata))

    summary = {
        **metadata,
        "entities": report_json["entities"],
        "modifications": report_json["modifications"],
        "stops_defined": report_json["stops_defined"],
    }
    (settings.output_dir / "metadata.json").write_text(
        json.dumps(summary, indent=1, ensure_ascii=False)
    )
    return summary


def run(settings: Settings, service_date: str | None = None) -> dict[str, object]:
    """Read the website, build the feed against the static GTFS, and write it out."""
    static = load_static_feed(
        FixSettings(static_gtfs_url=settings.static_gtfs_url, cache_dir=settings.cache_dir)
    )

    site = Site(
        base_url=settings.site_url,
        workers=settings.workers,
        requests_per_second=settings.requests_per_second,
    )
    lines = site.lines()
    logger.info("the website lists %d line directions", len(lines))
    detours = site.detours(lines)

    service_dates = service_dates_from(service_date or service_date_now(), settings.days)
    result = build(detours, static, service_dates, settings.build)
    logger.info(
        "%d entities, %d modifications, %d temporary stops",
        len(result.plans),
        sum(len(plan.modifications) for plan in result.plans),
        len(result.new_stops),
    )

    generated_at = datetime.now(UTC)
    feed = build_feed(result, service_dates, int(generated_at.timestamp()))

    metadata: dict[str, object] = {
        "generated_at": generated_at.isoformat(timespec="seconds"),
        "service_dates": list(service_dates),
        "source_url": settings.site_url,
        "lines_read": len(lines),
        "lines_detoured": len(detours),
        "requests_made": 1 + len(lines) + len(detours),
        "attribution": ATTRIBUTION,
    }

    return write_artifacts(
        result,
        feed.SerializeToString(),
        MessageToDict(feed, preserving_proto_field_name=True),
        metadata,
        settings,
    )
