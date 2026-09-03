"""Command line entry point."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .config import PASSWORD_ENV, USERNAME_ENV, Settings
from .pipeline import run
from .repair import RepairConfig


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tmfix",
        description=(
            "Repair STM TripModifications whose cancelled range is shorter than the detour."
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output"),
        help="where to write the repaired feed and the report (default: ./output)",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path(".cache"),
        help="where to keep the static feed and its parsed form (default: ./.cache)",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=RepairConfig.off_shape_threshold_m,
        help="metres from the detour shape beyond which a stop counts as unserved",
    )
    parser.add_argument(
        "--realtime-file",
        type=Path,
        help="read the realtime feed from this file instead of fetching it",
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
        repair=RepairConfig(off_shape_threshold_m=args.threshold),
    )

    if args.realtime_file is None and settings.credentials is None:
        print(
            f"The realtime feed needs basic auth. Set {USERNAME_ENV} and {PASSWORD_ENV}, "
            "or pass --realtime-file.",
            file=sys.stderr,
        )
        return 2

    summary = run(settings, realtime_path=args.realtime_file)
    print(json.dumps(summary, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
