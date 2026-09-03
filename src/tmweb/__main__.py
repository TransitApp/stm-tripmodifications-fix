"""Command line entry point."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from .config import Settings
from .pipeline import run


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tmweb",
        description="Build STM TripModifications from the detours the STM website publishes.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output-web"),
        help="where to write the feed and the report (default: ./output-web)",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path(".cache"),
        help="where to keep the static feed and its parsed form (default: ./.cache)",
    )
    parser.add_argument(
        "--service-date",
        help="the YYYYMMDD service date to write, instead of today in Montreal",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=8,
        help="how many website requests to have in flight at once (default: 8)",
    )
    parser.add_argument("--verbose", action="store_true", help="log what each step is doing")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(message)s",
    )

    settings = Settings(
        cache_dir=args.cache_dir,
        output_dir=args.output_dir,
        workers=args.workers,
    )
    summary = run(settings, service_date=args.service_date)
    print(json.dumps(summary, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
