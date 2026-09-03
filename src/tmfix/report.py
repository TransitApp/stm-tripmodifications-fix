"""Turning a repair result into something a person and a machine can read."""

from __future__ import annotations

from typing import Any

from .repair import EntityReport, RepairResult


def _range_text(bounds: tuple[int, int] | None) -> str:
    return "-" if bounds is None else f"[{bounds[0]}..{bounds[1]}]"


def _entity_json(entity: EntityReport) -> dict[str, Any]:
    return {
        "entity_id": entity.entity_id,
        "shape_id": entity.shape_id,
        "sample_trip_id": entity.trip_id,
        "skipped": entity.skipped.value if entity.skipped else None,
        "modifications": [
            {
                "index": modification.index,
                "declared_range": list(modification.declared_range)
                if modification.declared_range
                else None,
                "repaired_range": list(modification.repaired_range)
                if modification.repaired_range
                else None,
                "start_kept_because_of_travel_times": modification.kept_start,
                "skipped": modification.skipped.value if modification.skipped else None,
                "stops_added_to_cancellation": [
                    {
                        "stop_sequence": stop.stop_sequence,
                        "stop_id": stop.stop_id,
                        "distance_to_shape_m": round(stop.distance_m, 1),
                    }
                    for stop in modification.added_stops
                ],
                "replacement_stops_dropped": [
                    {
                        "stop_id": stop.stop_id,
                        "distance_to_shape_m": round(stop.distance_m, 1),
                    }
                    for stop in modification.dropped_stops
                ],
            }
            for modification in entity.modifications
        ],
    }


def build_json(result: RepairResult, metadata: dict[str, Any]) -> dict[str, Any]:
    """The full account of a run, for other programs to consume."""
    return {
        **metadata,
        "entities_examined": len(result.entities),
        "entities_repaired": len(result.repaired_entities),
        "entities_passed_through": len(result.skipped_entities),
        "repaired": [_entity_json(entity) for entity in result.repaired_entities],
        "passed_through": [_entity_json(entity) for entity in result.skipped_entities],
    }


def build_markdown(result: RepairResult, metadata: dict[str, Any]) -> str:
    """The same account, for a person."""
    lines: list[str] = ["# TripModifications repair report", ""]

    lines.append(f"- Feed timestamp: `{metadata.get('feed_timestamp', 'unknown')}`")
    lines.append(f"- Generated at: `{metadata.get('generated_at', 'unknown')}`")
    lines.append(f"- Off-shape threshold: {metadata.get('off_shape_threshold_m')} m")
    lines.append(f"- Entities examined: {len(result.entities)}")
    lines.append(f"- Entities repaired: {len(result.repaired_entities)}")
    lines.append("")

    repaired = result.repaired_entities
    if repaired:
        lines.append("## Repaired")
        lines.append("")
        for entity in repaired:
            lines.append(f"### `{entity.entity_id}`")
            lines.append("")
            for modification in entity.modifications:
                if not modification.changed:
                    continue
                declared = _range_text(modification.declared_range)
                repaired_range = _range_text(modification.repaired_range)
                if modification.added_stops:
                    lines.append(
                        f"- Modification {modification.index}: "
                        f"cancelled range {declared} to {repaired_range}"
                    )
                    for stop in modification.added_stops:
                        lines.append(
                            f"  - added stop_sequence {stop.stop_sequence} "
                            f"(`{stop.stop_id}`), {stop.distance_m:.0f} m from the detour shape"
                        )
                    if modification.kept_start:
                        lines.append(
                            "  - the range could only grow forward: the replacement stops "
                            "carry travel times counted from the stop before it"
                        )
                for stop in modification.dropped_stops:
                    lines.append(
                        f"- Modification {modification.index}: dropped replacement stop "
                        f"`{stop.stop_id}`, {stop.distance_m:.0f} m from the detour shape"
                    )
            lines.append("")
    else:
        lines.append("No repairs were needed.")
        lines.append("")

    passed = result.skipped_entities
    if passed:
        lines.append("## Passed through unchanged")
        lines.append("")
        lines.append("These could not be checked or could not be repaired safely.")
        lines.append("")
        for entity in passed:
            if entity.skipped:
                lines.append(f"- `{entity.entity_id}`: {entity.skipped.value}")
                continue
            for modification in entity.modifications:
                if modification.skipped:
                    lines.append(
                        f"- `{entity.entity_id}` modification {modification.index}: "
                        f"{modification.skipped.value}"
                    )
        lines.append("")

    if attribution := metadata.get("attribution"):
        lines.append("---")
        lines.append("")
        lines.append(attribution)
        lines.append("")

    return "\n".join(lines)
