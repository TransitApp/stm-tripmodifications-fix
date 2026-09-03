"""A stable fingerprint of what a repair report says was repaired.

The workflow redraws the PDF map report only when this changes, so the
fingerprint has to ignore everything that moves between runs without changing
the maps: when the run happened, which trip was sampled, and the measured
distances. What is left is what the pages actually show — which entities were
repaired, how each range moved, and which stops were added or dropped.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any


def repair_signature(report: dict[str, Any]) -> str:
    """Return a hex digest of the repairs a report describes."""
    entities = []

    for entity in report.get("repaired", []):
        modifications = [
            [
                modification.get("index"),
                modification.get("declared_range"),
                modification.get("repaired_range"),
                [
                    stop.get("stop_sequence")
                    for stop in modification.get("stops_added_to_cancellation", [])
                ],
                [stop.get("stop_id") for stop in modification.get("replacement_stops_dropped", [])],
            ]
            for modification in entity.get("modifications", [])
        ]
        entities.append([entity.get("entity_id"), entity.get("shape_id"), modifications])

    # The feed does not promise an order, so sort before hashing.
    entities.sort(key=lambda entity: entity[0] or "")
    packed = json.dumps(entities, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(packed.encode("utf-8")).hexdigest()


def repair_signature_of_file(path: Path) -> str:
    """Return the fingerprint of a report.json on disk."""
    with path.open(encoding="utf-8") as handle:
        return repair_signature(json.load(handle))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tmfix.signature",
        description="Print a fingerprint of the repairs in a report.json.",
    )
    parser.add_argument("report", type=Path, help="path to a report.json")
    arguments = parser.parse_args(argv)

    print(repair_signature_of_file(arguments.report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
