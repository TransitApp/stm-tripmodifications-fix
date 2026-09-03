"""Build the before/after map report.

    STM_API_USERNAME=... STM_API_PASSWORD=... python -m tmreport

Pass --realtime-file to draw a feed already on disk instead of fetching one.
The workflow does that, so a run fetches the realtime feed only once.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from .collect import collect
from .render import build_report

DEFAULT_OUTPUT = Path("output") / "stm-tripmodifications-report.pdf"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tmreport",
        description="Draw a before/after map for every repair the tool makes.",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path(".cache"),
        help="the repair tool's cache, so the static feed is not downloaded twice",
    )
    parser.add_argument(
        "--realtime-file",
        type=Path,
        help="read the realtime feed from this file instead of fetching it",
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


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    collected = collect(args.cache_dir, args.realtime_file)
    logging.info(
        "feed %s: %d entities examined, %d repaired, %d pages",
        collected.feed_timestamp,
        collected.entities_examined,
        collected.entities_repaired,
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
