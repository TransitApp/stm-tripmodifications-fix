"""One run: fetch, repair, write the artifacts."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from google.protobuf.json_format import MessageToDict
from google.transit import gtfs_realtime_pb2 as gtfs_rt

from . import download, report, static_feed
from .config import ATTRIBUTION, Settings
from .repair import RepairResult, repair_feed

logger = logging.getLogger(__name__)


def load_static_feed(settings: Settings) -> static_feed.StaticFeed:
    """Get the static feed, parsing it only when it changed since the last run."""
    downloaded = download.fetch_static_gtfs(settings.static_gtfs_url, settings.cache_dir)
    safe_version = "".join(char for char in downloaded.version if char.isalnum())[:64]
    cache_path = settings.cache_dir / f"static-{safe_version}.pickle"

    if not downloaded.changed:
        cached = static_feed.load_cache(cache_path)
        if cached is not None:
            logger.info("static feed unchanged, using cache (%d trips)", cached.trip_count)
            return cached

    logger.info("parsing static feed %s", downloaded.path)
    feed = static_feed.load_from_zip(downloaded.path)
    logger.info("parsed %d trips, %d stops", feed.trip_count, len(feed.stop_positions))

    for stale in settings.cache_dir.glob("static-*.pickle"):
        if stale != cache_path:
            stale.unlink(missing_ok=True)
    static_feed.save_cache(feed, cache_path)
    return feed


def write_artifacts(
    result: RepairResult,
    raw_feed: gtfs_rt.FeedMessage,
    settings: Settings,
) -> dict[str, object]:
    """Write the repaired feed, its JSON form and the report."""
    settings.output_dir.mkdir(parents=True, exist_ok=True)

    feed_timestamp = raw_feed.header.timestamp
    metadata: dict[str, object] = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "feed_timestamp": feed_timestamp,
        "feed_timestamp_iso": datetime.fromtimestamp(feed_timestamp, UTC).isoformat(
            timespec="seconds"
        )
        if feed_timestamp
        else None,
        "source_url": settings.realtime_url,
        "off_shape_threshold_m": settings.repair.off_shape_threshold_m,
        "attribution": ATTRIBUTION,
    }

    (settings.output_dir / "tripmodifications.pb").write_bytes(result.feed.SerializeToString())

    feed_json = MessageToDict(result.feed, preserving_proto_field_name=True)
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
        "entities_examined": report_json["entities_examined"],
        "entities_repaired": report_json["entities_repaired"],
        "entities_passed_through": report_json["entities_passed_through"],
    }
    (settings.output_dir / "metadata.json").write_text(
        json.dumps(summary, indent=1, ensure_ascii=False)
    )
    return summary


def run(settings: Settings, realtime_path: Path | None = None) -> dict[str, object]:
    """Fetch, repair and write. Reads the realtime feed from disk when given one."""
    feed = load_static_feed(settings)

    if realtime_path is not None:
        payload = realtime_path.read_bytes()
    else:
        payload = download.fetch_realtime(settings.realtime_url, settings.credentials)

    raw_feed = gtfs_rt.FeedMessage()
    raw_feed.ParseFromString(payload)

    result = repair_feed(raw_feed, feed, settings.repair)

    input_entities = sum(1 for entity in raw_feed.entity if entity.HasField("trip_modifications"))
    output_entities = sum(
        1 for entity in result.feed.entity if entity.HasField("trip_modifications")
    )
    if input_entities != output_entities:
        raise AssertionError(
            f"repair changed the entity count: {input_entities} in, {output_entities} out"
        )

    return write_artifacts(result, raw_feed, settings)
