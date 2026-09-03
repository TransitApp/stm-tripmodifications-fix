"""Whether a run should read the website.

The website is read twice an hour, at 5 and 35 minutes past. The clock decides,
not the age of what is published: the half hour starting at :05 is one turn, the
one starting at :35 the next, and a run reads only when the published feed was
written in an earlier turn. Going by the turn keeps the reads on the clock when
GitHub delays a scheduled run, and a run that is delayed or dropped still reads
as soon as one starts rather than costing a whole turn.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

# The minute past the hour the first turn of an hour starts at, and how long a
# turn lasts. Montreal is a whole number of hours off UTC, so the reads land on
# the same minutes on either clock.
FIRST_TURN_MINUTE = 5
TURN_MINUTES = 30


def turn_of(moment: datetime) -> int:
    """Which turn a moment falls in, counted in turns since the epoch."""
    minutes = int(moment.astimezone(UTC).timestamp() // 60)
    return (minutes - FIRST_TURN_MINUTE) // TURN_MINUTES


def is_due(generated_at: str, now: datetime | None = None) -> bool:
    """Whether a feed generated at that time belongs to an earlier turn."""
    published = datetime.fromisoformat(generated_at)
    if published.tzinfo is None:
        published = published.replace(tzinfo=UTC)
    return turn_of(published) < turn_of(now or datetime.now(UTC))


def is_due_file(path: Path, now: datetime | None = None) -> bool:
    """Whether the run a metadata.json on disk describes is a turn behind."""
    with path.open(encoding="utf-8") as handle:
        return is_due(json.load(handle)["generated_at"], now)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tmweb.due",
        description="Say whether the website is due to be read again.",
    )
    parser.add_argument("metadata", type=Path, help="path to a metadata.json")
    arguments = parser.parse_args(argv)

    print("yes" if is_due_file(arguments.metadata) else "no")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
