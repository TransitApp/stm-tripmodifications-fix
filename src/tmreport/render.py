"""Draw the before/after maps and assemble the PDF."""

from __future__ import annotations

import logging
import math
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import contextily as cx
import matplotlib
import matplotlib.patheffects as path_effects

matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.lines import Line2D

from tmfix.geometry import LatLon

from .collect import Collected, Page

USER_AGENT = "stm-tripmodifications-fix map report (+https://github.com/TransitApp/stm-tripmodifications-fix)"

# CartoDB Positron now returns watermarked "API KEY REQUIRED" tiles, so the
# basemap is Esri's grey canvas, which needs no key and reads the same way.
BASEMAP = "Esri.WorldGrayCanvas"
BASEMAP_MAX_ZOOM = 16
BASEMAP_CREDIT = "Basemap: Esri, HERE, Garmin, © OpenStreetMap contributors"
MONTREAL = ZoneInfo("America/Montreal")

# A4 landscape, the format a European or Canadian office prints without thinking.
PAGE_SIZE = (11.69, 8.27)

INK = "#1c1c1c"
MUTED = "#6b6b6b"
SCHEDULED = "#9aa0a6"
DETOUR = "#0b6e6e"
CANCELLED = "#c62828"
TEMPORARY = "#1565c0"
CHANGED = "#e8a33d"

EARTH_RADIUS_M = 6_378_137.0
MIN_SPAN_M = 350.0
PADDING = 0.22

# A long cancelled run would otherwise bury the map under a column of names.
# The markers still show every stop; the header already gives the count.
MAX_STOP_LABELS = 4


def to_mercator(point: LatLon) -> tuple[float, float]:
    """WGS84 to Web Mercator metres, which is what the tiles are drawn in."""
    x = math.radians(point.lon) * EARTH_RADIUS_M
    y = math.log(math.tan(math.pi / 4 + math.radians(point.lat) / 2)) * EARTH_RADIUS_M
    return x, y


def mercator_scale(latitude: float) -> float:
    """Metres on the ground per Mercator metre at a latitude."""
    return math.cos(math.radians(latitude))


def _clip(line: list[LatLon], bounds: tuple[float, float, float, float]):
    """Mercator points of a line, keeping the segments that touch the view."""
    left, bottom, right, top = bounds
    margin = max(right - left, top - bottom)
    points = [to_mercator(point) for point in line]

    kept: list[list[tuple[float, float]]] = []
    run: list[tuple[float, float]] = []
    for index, (x, y) in enumerate(points):
        inside = left - margin <= x <= right + margin and bottom - margin <= y <= top + margin
        if inside:
            if not run and index > 0:
                run.append(points[index - 1])
            run.append((x, y))
        elif run:
            run.append((x, y))
            kept.append(run)
            run = []
    if run:
        kept.append(run)
    return kept


def _extent(page: Page) -> tuple[float, float, float, float]:
    """A view holding the affected stops, their neighbours and the detour."""
    pattern = page.pattern
    by_sequence = {sequence: position for position, (sequence, _) in enumerate(pattern)}

    positions: set[int] = set()
    for stop_range in (page.declared_range, page.repaired_range):
        if stop_range is None:
            continue
        start = by_sequence.get(stop_range[0])
        end = by_sequence.get(stop_range[1])
        if start is not None and end is not None:
            positions.update(range(start, end + 1))

    # Two stops either side, so the reader sees where the detour rejoins.
    if positions:
        low, high = min(positions), max(positions)
        positions.update(range(max(0, low - 2), min(len(pattern), high + 3)))

    points: list[LatLon] = []
    for position in positions:
        if (location := page.stop_positions.get(pattern[position][1])) is not None:
            points.append(location)
    for stop_id in set(page.before.replacement_stop_ids) | set(page.after.replacement_stop_ids):
        if (location := page.stop_positions.get(stop_id)) is not None:
            points.append(location)

    if not points:
        points = [location for location in page.stop_positions.values()]

    xs, ys = zip(*(to_mercator(point) for point in points), strict=True)
    left, right, bottom, top = min(xs), max(xs), min(ys), max(ys)

    # The detour geometry around those stops is part of the story, so pull in the
    # shape near them. The window is fixed before the loop: growing it as points
    # are accepted would let one distant vertex drag in the whole route.
    scale = mercator_scale(points[0].lat)
    margin = 0.35 * max(right - left, top - bottom, MIN_SPAN_M / scale)
    window = (left - margin, bottom - margin, right + margin, top + margin)
    for x, y in (to_mercator(point) for point in page.shape):
        if window[0] <= x <= window[2] and window[1] <= y <= window[3]:
            left, right = min(left, x), max(right, x)
            bottom, top = min(bottom, y), max(top, y)

    width = max(right - left, MIN_SPAN_M / scale)
    height = max(top - bottom, MIN_SPAN_M / scale)
    # Match the panel's aspect so neither axis is stretched.
    aspect = 1.05
    if width / height < aspect:
        width = height * aspect
    else:
        height = width / aspect

    centre_x, centre_y = (left + right) / 2, (bottom + top) / 2
    width *= 1 + PADDING
    height *= 1 + PADDING
    return (
        centre_x - width / 2,
        centre_y - height / 2,
        centre_x + width / 2,
        centre_y + height / 2,
    )


def _zoom_for(bounds: tuple[float, float, float, float]) -> int:
    """Tile zoom that fills the panel, clamped to what the provider serves."""
    left, _, right, _ = bounds
    world = 2 * math.pi * EARTH_RADIUS_M
    # A panel is roughly 1,100 device pixels wide at the DPI this is saved at.
    zoom = math.log2(world / (right - left) * 1100 / 256)
    return max(1, min(BASEMAP_MAX_ZOOM, round(zoom)))


def _scale_bar(ax, bounds: tuple[float, float, float, float], latitude: float) -> None:
    """A bar whose length is a round number of ground metres."""
    left, bottom, right, top = bounds
    ground_width = (right - left) * mercator_scale(latitude)

    # The longest round bar that still leaves the map readable.
    choices = (50, 100, 200, 250, 500, 1000, 2000)
    fitting = [candidate for candidate in choices if candidate <= ground_width * 0.3]
    length_m = fitting[-1] if fitting else choices[0]

    length = length_m / mercator_scale(latitude)
    x0 = left + (right - left) * 0.05
    y0 = bottom + (top - bottom) * 0.05

    ax.plot([x0, x0 + length], [y0, y0], color=INK, linewidth=2.4, solid_capstyle="butt", zorder=8)
    for x in (x0, x0 + length):
        ax.plot(
            [x, x],
            [y0 - (top - bottom) * 0.008, y0 + (top - bottom) * 0.008],
            color=INK,
            linewidth=2.4,
            zorder=8,
        )
    ax.text(
        x0 + length / 2,
        y0 + (top - bottom) * 0.016,
        f"{length_m} m",
        ha="center",
        va="bottom",
        fontsize=7,
        color=INK,
        zorder=8,
        path_effects=[path_effects.withStroke(linewidth=2.6, foreground="white")],
    )


def _place_labels(ax, labelled, bounds) -> None:
    """Label the affected stops, nudging each one clear of the last.

    Stops on a bus route come a block apart, so labels stack unless they are
    pushed off each other. Working down the map and remembering the last label's
    box is enough here; a full collision solver would be more machinery than
    fourteen labels deserve.
    """
    left, bottom, right, top = bounds
    height = top - bottom
    # Roughly the vertical room one line of 6.6 pt text needs in data units.
    line_height = height * 0.030

    taken: list[tuple[float, float]] = []
    for x, y, text, colour in sorted(labelled, key=lambda item: -item[1]):
        to_the_right = x < (left + right) / 2
        label_y = y
        for used_x, used_y in taken:
            same_side = (used_x < (left + right) / 2) == to_the_right
            if same_side and abs(label_y - used_y) < line_height:
                label_y = used_y - line_height
        taken.append((x, label_y))

        label_y = max(label_y, bottom + height * 0.02)
        offset_y = (label_y - y) / height * ax.bbox.height
        ax.annotate(
            text,
            xy=(x, y),
            xytext=(9 if to_the_right else -9, offset_y),
            textcoords="offset points",
            fontsize=6.6,
            color=colour,
            ha="left" if to_the_right else "right",
            va="center",
            zorder=9,
            annotation_clip=True,
            path_effects=[path_effects.withStroke(linewidth=2.6, foreground="white")],
        )


def _draw_panel(
    ax,
    page: Page,
    panel,
    bounds,
    title: str,
    highlight: set[str],
    use_basemap: bool = True,
) -> None:
    """One map: the scheduled path, the detour, and the stops as this side claims them."""
    left, bottom, right, top = bounds
    ax.set_xlim(left, right)
    ax.set_ylim(bottom, top)
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_edgecolor("#c9c9c9")
        spine.set_linewidth(0.8)

    if use_basemap:
        try:
            cx.add_basemap(
                ax,
                source=cx.providers.Esri.WorldGrayCanvas,
                attribution=False,
                zoom=_zoom_for(bounds),
                headers={"User-Agent": USER_AGENT},
            )
        except Exception as error:  # a map without streets is poor, but better than no page
            logging.warning("basemap tiles unavailable: %s", error)
            ax.set_facecolor("#f2f2f0")
    else:
        ax.set_facecolor("#f2f2f0")

    for run in _clip(page.scheduled_shape, bounds):
        xs, ys = zip(*run, strict=True)
        ax.plot(xs, ys, color=SCHEDULED, linewidth=5.5, alpha=0.9, zorder=2, solid_capstyle="round")
    for run in _clip(page.shape, bounds):
        xs, ys = zip(*run, strict=True)
        ax.plot(xs, ys, color=DETOUR, linewidth=2.4, zorder=3, solid_capstyle="round")

    pattern = page.pattern
    labelled: list[tuple[float, float, str, str]] = []
    already_labelled: set[str] = set()
    candidates: list[tuple[int, str, float, float, str, str]] = []

    for position, (sequence, stop_id) in enumerate(pattern):
        location = page.stop_positions.get(stop_id)
        if location is None:
            continue
        x, y = to_mercator(location)
        if not (left <= x <= right and bottom <= y <= top):
            continue

        cancelled = position in panel.cancelled_positions
        changed = stop_id in highlight

        if changed:
            ax.scatter(
                [x], [y], s=170, facecolors="none", edgecolors=CHANGED, linewidths=2.0, zorder=5
            )
        if cancelled:
            ax.scatter(
                [x], [y], s=58, facecolors="white", edgecolors=CANCELLED, linewidths=1.7, zorder=6
            )
            ax.plot(
                [x], [y], marker="x", color=CANCELLED, markersize=4.4, markeredgewidth=1.5, zorder=7
            )
        else:
            ax.scatter([x], [y], s=34, facecolors=INK, edgecolors="white", linewidths=0.9, zorder=6)

        if changed or cancelled:
            name = page.stop_names.get(stop_id, stop_id)
            candidates.append(
                (sequence, stop_id, x, y, f"{sequence}. {name}", CANCELLED if cancelled else INK)
            )

    # Name the ends of the affected run; the ones between them would only stack.
    if len(candidates) > MAX_STOP_LABELS:
        candidates.sort()
        kept = [candidates[0], candidates[-1]]
    else:
        kept = candidates
    for _, stop_id, x, y, text, colour in kept:
        already_labelled.add(stop_id)
        labelled.append((x, y, text, colour))

    replacements: list[tuple[str, float, float]] = []
    for stop_id in panel.replacement_stop_ids:
        location = page.stop_positions.get(stop_id)
        if location is None:
            continue
        x, y = to_mercator(location)
        if not (left <= x <= right and bottom <= y <= top):
            continue
        if stop_id in highlight:
            ax.scatter(
                [x], [y], s=210, facecolors="none", edgecolors=CHANGED, linewidths=2.0, zorder=5
            )
        ax.scatter(
            [x],
            [y],
            s=120,
            marker="*",
            facecolors=TEMPORARY,
            edgecolors="white",
            linewidths=0.8,
            zorder=7,
        )
        replacements.append((stop_id, x, y))

    # A long list of replacement stops gets the same treatment as a long
    # cancelled run: name the ends, and always name the ones the repair touched.
    if len(replacements) > MAX_STOP_LABELS:
        chosen = [replacements[0], replacements[-1]]
        chosen += [item for item in replacements[1:-1] if item[0] in highlight]
    else:
        chosen = replacements
    for stop_id, x, y in chosen:
        if stop_id in already_labelled:
            continue
        already_labelled.add(stop_id)
        labelled.append((x, y, page.stop_names.get(stop_id, stop_id), TEMPORARY))

    _place_labels(ax, labelled, bounds)
    _scale_bar(ax, bounds, (page.pattern and _first_latitude(page)) or 45.5)
    ax.set_title(title, fontsize=10, color=INK, pad=7, fontweight="bold")


def _first_latitude(page: Page) -> float:
    for _, stop_id in page.pattern:
        if (location := page.stop_positions.get(stop_id)) is not None:
            return location.lat
    return 45.5


def _legend(fig) -> None:
    handles = [
        Line2D([], [], color=SCHEDULED, linewidth=5.5, label="Scheduled route"),
        Line2D([], [], color=DETOUR, linewidth=2.4, label="Detour shape from the feed"),
        Line2D(
            [],
            [],
            marker="o",
            color="none",
            markerfacecolor=INK,
            markeredgecolor="white",
            markersize=6,
            label="Stop still served",
        ),
        Line2D(
            [],
            [],
            marker="o",
            color="none",
            markerfacecolor="white",
            markeredgecolor=CANCELLED,
            markeredgewidth=1.6,
            markersize=7,
            label="Stop cancelled",
        ),
        Line2D(
            [],
            [],
            marker="*",
            color="none",
            markerfacecolor=TEMPORARY,
            markeredgecolor="white",
            markersize=12,
            label="Replacement stop",
        ),
        Line2D(
            [],
            [],
            marker="o",
            color="none",
            markerfacecolor="none",
            markeredgecolor=CHANGED,
            markeredgewidth=2.0,
            markersize=12,
            label="Changed by the repair",
        ),
    ]
    fig.legend(
        handles=handles,
        loc="lower center",
        ncol=6,
        frameon=False,
        fontsize=7.8,
        bbox_to_anchor=(0.5, 0.028),
        handletextpad=0.6,
        columnspacing=1.8,
    )


def _headline(page: Page) -> tuple[str, str]:
    declared = page.declared_range
    repaired = page.repaired_range
    route = page.route.short_name or "?"

    if page.range_grew and declared and repaired:
        headline = (
            f"Route {route}: cancelled range [{declared[0]}..{declared[1]}] "
            f"should be [{repaired[0]}..{repaired[1]}]"
        )
    else:
        headline = f"Route {route}: a replacement stop is not on the detour"

    bits = []
    if page.report.added_stops:
        count = len(page.report.added_stops)
        worst = max(stop.distance_m for stop in page.report.added_stops)
        noun = "stop" if count == 1 else "stops"
        verb = "sits" if count == 1 else "sit"
        bits.append(
            f"{count} {noun} kept in the trip {verb} up to {worst:.0f} m from the detour shape"
        )
    for dropped in page.report.dropped_stops:
        bits.append(f"replacement stop {dropped.stop_id} is {dropped.distance_m:.0f} m from it")
    return headline, "; ".join(bits) + "."


def draw_page(pdf: PdfPages, page: Page, index: int, total: int, use_basemap: bool = True) -> None:
    fig = plt.figure(figsize=PAGE_SIZE)
    fig.patch.set_facecolor("white")

    headline, evidence = _headline(page)
    fig.text(0.055, 0.960, headline, fontsize=14, color=INK, fontweight="bold", va="top")
    fig.text(0.055, 0.918, page.route.title(), fontsize=9.5, color=MUTED, va="top")
    fig.text(
        0.055,
        0.890,
        f"{page.entity_id}   ·   modification {page.modification_index}   ·   trip {page.trip_id}",
        fontsize=8,
        color=MUTED,
        va="top",
        family="monospace",
    )
    fig.text(0.055, 0.860, evidence, fontsize=8.6, color=INK, va="top")

    bounds = _extent(page)
    changed = {stop.stop_id for stop in page.report.dropped_stops}
    changed |= {stop.stop_id for stop in page.report.added_stops}

    left_ax = fig.add_axes([0.055, 0.115, 0.425, 0.685])
    right_ax = fig.add_axes([0.520, 0.115, 0.425, 0.685])

    _draw_panel(left_ax, page, page.before, bounds, "STM's feed as published", changed, use_basemap)
    _draw_panel(right_ax, page, page.after, bounds, "After repair", changed, use_basemap)

    _legend(fig)
    fig.text(
        0.055,
        0.012,
        f"{BASEMAP_CREDIT}.  Transit data: Société de transport de Montréal, CC-BY 4.0.",
        fontsize=6.4,
        color=MUTED,
    )
    fig.text(0.945, 0.012, f"{index} of {total}", fontsize=6.4, color=MUTED, ha="right")

    pdf.savefig(fig)
    plt.close(fig)


def draw_cover(pdf: PdfPages, collected: Collected) -> None:
    fig = plt.figure(figsize=PAGE_SIZE)
    fig.patch.set_facecolor("white")

    fig.text(
        0.055, 0.935, "STM TripModifications", fontsize=23, color=INK, fontweight="bold", va="top"
    )
    fig.text(
        0.055,
        0.882,
        "Detours whose cancelled range is shorter than the detour itself",
        fontsize=12.5,
        color=MUTED,
        va="top",
    )

    explanation = (
        "In a detour at the end of a line, the cancelled range collapses to a single stop "
        "while the detour shape published in the same entity skips a longer run of stops. "
        "The stops in between stay in the trip although the vehicle never reaches them, and "
        "a stop the vehicle no longer serves is sometimes re-added as a replacement stop. "
        "Detours in the middle of a line are correct. Each page below shows one affected "
        "modification: the same detour shape in both panels, the stops as the feed claims "
        "them on the left, and as the shape implies them on the right."
    )
    fig.text(
        0.055, 0.828, _wrap(explanation, 118), fontsize=9.4, color=INK, va="top", linespacing=1.55
    )

    feed_time = datetime.fromtimestamp(collected.feed_timestamp, UTC).astimezone(MONTREAL)
    facts = (
        f"Feed timestamp {collected.feed_timestamp} "
        f"({feed_time.strftime('%Y-%m-%d %H:%M')} America/Montreal)   ·   "
        f"{collected.entities_examined} entities examined   ·   "
        f"{collected.entities_repaired} repaired"
    )
    fig.text(0.055, 0.672, facts, fontsize=8.6, color=MUTED, va="top")

    threshold = (
        f"A stop is taken to be unserved when it lies more than {collected.threshold_m:.0f} m from "
        "the detour shape. Stops the vehicle does serve measure 0–26 m from it; the errors below "
        "measure 94–471 m, so the reading does not depend on where in that gap the line is drawn."
    )
    fig.text(
        0.055, 0.628, _wrap(threshold, 118), fontsize=8.6, color=MUTED, va="top", linespacing=1.5
    )

    _cover_table(fig, collected)

    fig.text(
        0.055,
        0.030,
        "Source data: Société de transport de Montréal, licensed CC-BY 4.0. "
        "This analysis is unofficial and is not endorsed by the STM.",
        fontsize=7,
        color=MUTED,
    )
    pdf.savefig(fig)
    plt.close(fig)


def _cover_table(fig, collected: Collected) -> None:
    ax = fig.add_axes([0.055, 0.075, 0.89, 0.47])
    ax.axis("off")

    columns = [0.0, 0.30, 0.42, 0.545, 0.70]
    headers = ["Entity", "Route", "Declared", "Repaired", "What changed"]
    y = 1.0
    for x, header in zip(columns, headers, strict=True):
        ax.text(
            x,
            y,
            header,
            fontsize=8,
            color=MUTED,
            fontweight="bold",
            transform=ax.transAxes,
            va="top",
        )
    ax.axhline(y=0.968, xmin=0, xmax=1, color="#d8d8d8", linewidth=0.8)

    row_height = 0.925 / max(len(collected.pages) + 1, 1)
    y = 0.945
    section_drawn = False
    for page in collected.pages:
        y -= row_height
        if not page.range_grew and not section_drawn:
            section_drawn = True
            y -= row_height * 0.5
            ax.axhline(y=y + row_height * 0.30, xmin=0, xmax=1, color="#e4e4e4", linewidth=0.7)
            ax.text(
                0.0,
                y + row_height * 0.10,
                "Replacement stop not on the detour",
                fontsize=7.4,
                color=MUTED,
                style="italic",
                transform=ax.transAxes,
                va="top",
            )
            y -= row_height * 0.75

        declared = page.declared_range
        repaired = page.repaired_range
        changes = []
        if page.report.added_stops:
            count = len(page.report.added_stops)
            changes.append(f"+{count} stop{'' if count == 1 else 's'} cancelled")
        for dropped in page.report.dropped_stops:
            changes.append(f"dropped {dropped.stop_id} ({dropped.distance_m:.0f} m)")

        cells = [
            page.entity_id,
            page.route.short_name or "?",
            f"[{declared[0]}..{declared[1]}]" if declared else "—",
            f"[{repaired[0]}..{repaired[1]}]" if repaired else "—",
            ", ".join(changes),
        ]
        for index, (x, cell) in enumerate(zip(columns, cells, strict=True)):
            ax.text(
                x,
                y,
                cell,
                fontsize=7.4,
                color=INK,
                transform=ax.transAxes,
                va="top",
                family="monospace" if index in (0, 2, 3) else None,
            )


def _wrap(text: str, width: int) -> str:
    import textwrap

    return "\n".join(textwrap.wrap(text, width))


def build_report(
    collected: Collected,
    output_path,
    use_basemap: bool = True,
    tile_cache_dir: str = ".tilecache",
) -> int:
    """Write the PDF and return its page count."""
    if use_basemap:
        cx.set_cache_dir(tile_cache_dir)
        cx.tile.USER_AGENT = USER_AGENT

    with PdfPages(output_path) as pdf:
        draw_cover(pdf, collected)
        for index, page in enumerate(collected.pages, start=1):
            draw_page(pdf, page, index, len(collected.pages), use_basemap)
        info = pdf.infodict()
        info["Title"] = "STM TripModifications — detours whose cancelled range is too short"
        info["Subject"] = f"Feed timestamp {collected.feed_timestamp}"
        info["Creator"] = "stm-tripmodifications-fix"
    return len(collected.pages) + 1
