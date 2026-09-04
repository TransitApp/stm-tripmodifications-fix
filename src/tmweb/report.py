"""What one run found, for a person and for another program."""

from __future__ import annotations

from typing import Any

from .build import BuildResult, PatternPlan
from .feed import entity_id_for


def _plan_json(plan: PatternPlan) -> dict[str, Any]:
    return {
        "entity_id": entity_id_for(plan),
        "route_pattern_id": plan.route_pattern_id,
        "route_id": plan.route_id,
        "direction": plan.direction,
        "line_description": plan.line_description,
        "trips": len(plan.trip_ids),
        "modifications": [
            {
                "start_stop_sequence": modification.start_sequence,
                "end_stop_sequence": modification.end_sequence,
                "cancelled_stop_ids": modification.cancelled_stop_ids,
                "replacement_stop_ids": modification.replacement_stop_ids,
                "kept_stop_id": modification.anchor_stop_id,
            }
            for modification in plan.modifications
        ],
    }


def build_json(result: BuildResult, metadata: dict[str, Any]) -> dict[str, Any]:
    """The full account of a run, for other programs to consume."""
    return {
        **metadata,
        "entities": len(result.plans),
        "modifications": sum(len(plan.modifications) for plan in result.plans),
        "stops_defined": len(result.defined_stops),
        "lines_with_nothing_to_say": len(result.skipped),
        "detours": [_plan_json(plan) for plan in result.plans],
        "skipped_lines": [
            {"line": skipped.line_key, "reason": skipped.reason} for skipped in result.skipped
        ],
        "replacement_stops_left_out": [
            {"line": line, "stop_ids": stop_ids}
            for line, stop_ids in sorted(result.dropped_replacements.items())
        ],
    }


def _dates(metadata: dict[str, Any]) -> str:
    """The run of dates a run wrote, as a reader wants to see it."""
    dates = metadata.get("service_dates") or []
    if not dates:
        return "unknown"
    if len(dates) == 1:
        return f"`{dates[0]}`"
    return f"`{dates[0]}` to `{dates[-1]}`, {len(dates)} days"


def build_markdown(result: BuildResult, metadata: dict[str, Any]) -> str:
    """The same account, for a person."""
    lines: list[str] = ["# TripModifications from the STM website", ""]

    lines.append(f"- Service dates: {_dates(metadata)}")
    lines.append(f"- Generated at: `{metadata.get('generated_at', 'unknown')}`")
    lines.append(f"- Line directions read: {metadata.get('lines_read', 'unknown')}")
    lines.append(f"- Line directions detoured: {metadata.get('lines_detoured', 'unknown')}")
    lines.append(f"- Requests made to stm.info: {metadata.get('requests_made', 'unknown')}")
    lines.append(f"- Entities written: {len(result.plans)}")
    lines.append(
        f"- Modifications written: {sum(len(plan.modifications) for plan in result.plans)}"
    )
    lines.append(f"- Stops the feed defines: {len(result.defined_stops)}")
    lines.append("")

    if result.plans:
        lines.append("## Detours")
        lines.append("")
        for plan in result.plans:
            lines.append(
                f"### `{entity_id_for(plan)}` — {plan.route_id} {plan.direction} "
                f"{plan.line_description}, {len(plan.trip_ids)} trips"
            )
            lines.append("")
            for modification in plan.modifications:
                span = f"[{modification.start_sequence}..{modification.end_sequence}]"
                dropped = ", ".join(f"`{stop}`" for stop in modification.cancelled_stop_ids)
                served = ", ".join(f"`{stop}`" for stop in modification.replacement_stop_ids)
                if modification.anchor_stop_id:
                    lines.append(
                        f"- {span}: nothing dropped. `{modification.anchor_stop_id}` is named as "
                        "the span and served again, so the trip calls at it as before."
                    )
                else:
                    lines.append(f"- {span}: drops {dropped or 'nothing'}")
                if served:
                    lines.append(f"  - serves {served}")
            lines.append("")
    else:
        lines.append("The website reports no detour that maps onto a trip running today.")
        lines.append("")

    if result.dropped_replacements:
        lines.append("## Replacement stops left out")
        lines.append("")
        lines.append("These sit on none of the line's detours, so where they belong is unclear.")
        lines.append("")
        for line, stop_ids in sorted(result.dropped_replacements.items()):
            lines.append(f"- {line}: {', '.join(f'`{stop}`' for stop in stop_ids)}")
        lines.append("")

    if result.skipped:
        lines.append("## Lines that produced nothing")
        lines.append("")
        for skipped in result.skipped:
            lines.append(f"- {skipped.line_key}: {skipped.reason}")
        lines.append("")

    if attribution := metadata.get("attribution"):
        lines.append("---")
        lines.append("")
        lines.append(attribution)
        lines.append("")

    return "\n".join(lines)
