"""How long ago a published run happened.

The workflow reads the website only once the published feed has aged out, and
measures that against the feed's own `generated_at` rather than against the
clock, so a scheduled run GitHub delays or drops does not cost a whole turn.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path


def minutes_since(generated_at: str, now: datetime | None = None) -> int:
    """Whole minutes between an ISO timestamp and now."""
    published = datetime.fromisoformat(generated_at)
    if published.tzinfo is None:
        published = published.replace(tzinfo=UTC)
    elapsed = (now or datetime.now(UTC)) - published
    return int(elapsed.total_seconds() // 60)


def minutes_since_file(path: Path, now: datetime | None = None) -> int:
    """The age of a metadata.json on disk, in whole minutes."""
    with path.open(encoding="utf-8") as handle:
        return minutes_since(json.load(handle)["generated_at"], now)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tmweb.age",
        description="Print how many minutes ago a metadata.json was generated.",
    )
    parser.add_argument("metadata", type=Path, help="path to a metadata.json")
    arguments = parser.parse_args(argv)

    print(minutes_since_file(arguments.metadata))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
