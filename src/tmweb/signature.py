"""A stable fingerprint of the detours a website report describes.

The workflow redraws the map report only when this changes, so the fingerprint
holds what the pages draw — which entity, which span, which stops are skipped
and which are served instead — and nothing that moves on its own between runs,
such as when the run happened, the service date or how many trips are running.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any


def detour_signature(report: dict[str, Any]) -> str:
    """Return a hex digest of the detours a report describes."""
    detours = []

    for detour in report.get("detours", []):
        modifications = [
            [
                modification.get("start_stop_sequence"),
                modification.get("end_stop_sequence"),
                modification.get("cancelled_stop_ids"),
                modification.get("replacement_stop_ids"),
            ]
            for modification in detour.get("modifications", [])
        ]
        detours.append([detour.get("entity_id"), modifications])

    # The feed does not promise an order, so sort before hashing.
    detours.sort(key=lambda detour: detour[0] or "")
    packed = json.dumps(detours, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(packed.encode("utf-8")).hexdigest()


def detour_signature_of_file(path: Path) -> str:
    """Return the fingerprint of a website report.json on disk."""
    with path.open(encoding="utf-8") as handle:
        return detour_signature(json.load(handle))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tmweb.signature",
        description="Print a fingerprint of the detours in a website report.json.",
    )
    parser.add_argument("report", type=Path, help="path to a report.json")
    arguments = parser.parse_args(argv)

    print(detour_signature_of_file(arguments.report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
