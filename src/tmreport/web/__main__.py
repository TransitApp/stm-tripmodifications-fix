"""Draw the map report for the feed built from the STM website.

    python -m tmreport.web

It reads a feed already written by `tmweb` rather than the website, so it
costs stm.info nothing and can be run against any published copy of the feed.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from tmfix.config import Settings
from tmfix.pipeline import load_static_feed

from .collect import collect
from .render import build_report

DEFAULT_FEED = Path("output-web") / "tripmodifications.pb"
DEFAULT_OUTPUT = Path("output-web") / "stm-detours-report.pdf"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tmreport.web",
        description="Draw a map of every detour the website feed describes.",
    )
    parser.add_argument(
        "--feed",
        type=Path,
        default=DEFAULT_FEED,
        help=f"the feed tmweb wrote (default: {DEFAULT_FEED})",
    )
    parser.add_argument(
        "--metadata",
        type=Path,
        help="the metadata.json beside the feed, for the cover (default: next to the feed)",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path(".cache"),
        help="the tools' cache, so the static feed is not downloaded twice",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"where to write the PDF (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--tile-cache-dir",
        default=".tilecache",
        help="where downloaded basemap tiles are kept (default: ./.tilecache)",
    )
    parser.add_argument(
        "--no-basemap",
        action="store_true",
        help="draw without street tiles, which is faster and needs no network",
    )
    return parser


def _metadata(path: Path | None, feed_path: Path) -> dict[str, object]:
    path = path or feed_path.parent / "metadata.json"
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    static = load_static_feed(Settings(cache_dir=args.cache_dir))
    collected = collect(
        args.feed,
        static,
        args.cache_dir / "static-gtfs.zip",
        _metadata(args.metadata, args.feed),
    )
    logging.info(
        "%d entities, %d modifications, %d pages to draw",
        collected.entities,
        collected.modifications,
        len(collected.pages),
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    pages = build_report(
        collected,
        args.output,
        use_basemap=not args.no_basemap,
        tile_cache_dir=args.tile_cache_dir,
    )
    logging.info("wrote %s (%d pages)", args.output, pages)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
