"""Draw one map per detour and assemble the PDF."""

from __future__ import annotations

from datetime import UTC, datetime

import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.lines import Line2D

from tmfix.geometry import LatLon

from ..draw import (
    BASEMAP_CREDIT,
    CANCELLED,
    CHANGED,
    DETOUR,
    INK,
    MAX_STOP_LABELS,
    MONTREAL,
    MUTED,
    PAGE_SIZE,
    SCHEDULED,
    TEMPORARY,
    Bounds,
    basemap,
    clip,
    fit_bounds,
    frame,
    place_labels,
    scale_bar,
    set_tile_cache,
    to_mercator,
    wrap,
)
from .collect import Collected, Detour

# The map takes the left half of the page and the stop lists the right.
MAP_BOX = (0.055, 0.115, 0.545, 0.700)
LIST_BOX = (0.640, 0.115, 0.305, 0.700)
MAP_ASPECT = (MAP_BOX[2] * PAGE_SIZE[0]) / (MAP_BOX[3] * PAGE_SIZE[1])

# The list beside the map holds this many lines before it starts counting the
# rest. The contents starts on the cover and continues over its own pages.
MAX_LIST_LINES = 30
COVER_ROWS = 20
CONTENTS_ROWS = 38


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _extent(page: Detour) -> Bounds:
    """A view holding the span, a stop either side of it, and the replacements.

    The detour shape is not pulled in the way the repair report pulls in its
    own: this one is the whole trip's shape with the detour spliced into it, so
    reaching for it would drag in the rest of the line.
    """
    points: list[LatLon] = []
    for sequence, stop_id in page.pattern:
        # Two stops either side, so the reader sees where the detour rejoins.
        if not page.start_sequence - 3 < sequence < page.end_sequence + 3:
            continue
        if (location := page.stop_positions.get(stop_id)) is not None:
            points.append(location)
    for stop_id in page.replacement_stop_ids:
        if (location := page.stop_positions.get(stop_id)) is not None:
            points.append(location)

    if not points:
        points = list(page.stop_positions.values())
    return fit_bounds(points, aspect=MAP_ASPECT)


def _draw_map(ax, page: Detour, bounds: Bounds, use_basemap: bool = True) -> None:
    """The scheduled road in grey, the road the detour takes in teal, and the stops."""
    left, bottom, right, top = bounds
    frame(ax, bounds)
    basemap(ax, bounds, use_basemap)

    for run in clip(page.scheduled_shape, bounds):
        xs, ys = zip(*run, strict=True)
        ax.plot(xs, ys, color=SCHEDULED, linewidth=5.5, alpha=0.9, zorder=2, solid_capstyle="round")
    for run in clip(page.detour_shape, bounds):
        xs, ys = zip(*run, strict=True)
        ax.plot(xs, ys, color=DETOUR, linewidth=2.4, zorder=3, solid_capstyle="round")

    skipped = {stop_id for _, stop_id in page.skipped}
    labelled: list[tuple[float, float, str, str]] = []
    candidates: list[tuple[int, float, float, str]] = []

    for sequence, stop_id in page.pattern:
        location = page.stop_positions.get(stop_id)
        if location is None:
            continue
        x, y = to_mercator(location)
        if not (left <= x <= right and bottom <= y <= top):
            continue

        if stop_id in skipped:
            ax.scatter(
                [x], [y], s=58, facecolors="white", edgecolors=CANCELLED, linewidths=1.7, zorder=6
            )
            ax.plot(
                [x], [y], marker="x", color=CANCELLED, markersize=4.4, markeredgewidth=1.5, zorder=7
            )
            name = page.stop_names.get(stop_id, stop_id)
            candidates.append((sequence, x, y, f"{sequence}. {name}"))
        else:
            ax.scatter([x], [y], s=34, facecolors=INK, edgecolors="white", linewidths=0.9, zorder=6)

    # Name the ends of the skipped run; the ones between them would only stack.
    if len(candidates) > MAX_STOP_LABELS:
        candidates.sort()
        candidates = [candidates[0], candidates[-1]]
    for _, x, y, text in candidates:
        labelled.append((x, y, text, CANCELLED))

    replacements: list[tuple[str, float, float]] = []
    for stop_id in page.replacement_stop_ids:
        location = page.stop_positions.get(stop_id)
        if location is None:
            continue
        x, y = to_mercator(location)
        if not (left <= x <= right and bottom <= y <= top):
            continue
        if stop_id in page.temporary_stop_ids:
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

    if len(replacements) > MAX_STOP_LABELS:
        replacements = [replacements[0], replacements[-1]]
    for stop_id, x, y in replacements:
        if stop_id in skipped:
            continue
        labelled.append((x, y, page.stop_names.get(stop_id, stop_id), TEMPORARY))

    place_labels(ax, labelled, bounds)
    scale_bar(ax, bounds, _first_latitude(page))


def _first_latitude(page: Detour) -> float:
    for _, stop_id in page.pattern:
        if (location := page.stop_positions.get(stop_id)) is not None:
            return location.lat
    return 45.5


def _list_lines(page: Detour) -> list[tuple[str, str, bool]]:
    """The two stop lists as rows of prefix, text and whether it is a heading."""
    kept = {stop_id: sequence for sequence, stop_id in page.kept}
    rows: list[tuple[str, str, bool]] = []

    if page.skipped:
        rows.append((f"Skips {_plural(len(page.skipped), 'stop')}", "", True))
        rows += [
            (f"{sequence}.", page.stop_names.get(stop_id, stop_id), False)
            for sequence, stop_id in page.skipped
        ]
    else:
        rows.append(("Skips no stops", "", True))
        rows.append(("", "the detour only adds stops to the trip", False))

    rows.append(("", "", False))
    if page.replacement_stop_ids:
        rows.append((f"Serves {_plural(len(page.replacement_stop_ids), 'stop')}", "", True))
        for stop_id in page.replacement_stop_ids:
            # A stop of the span that is served again keeps its own sequence,
            # which says it is the trip's own stop rather than a new one.
            prefix = (
                f"{kept[stop_id]}."
                if stop_id in kept
                else ("★" if stop_id in page.temporary_stop_ids else "·")
            )
            rows.append((prefix, page.stop_names.get(stop_id, stop_id), False))
    else:
        rows.append(("Serves nothing instead", "", True))
        rows.append(("", "the stops are dropped outright", False))

    if page.kept:
        rows.append(("", "", False))
        rows.append(
            (
                "",
                "A numbered stop above is one the span names and the detour still "
                "passes, so the trip calls at it as before.",
                False,
            )
        )
    return rows


def _draw_lists(ax, page: Detour) -> None:
    """The stops the detour skips and the stops it serves instead, named."""
    ax.axis("off")

    rows = _list_lines(page)
    if len(rows) > MAX_LIST_LINES:
        hidden = len(rows) - MAX_LIST_LINES + 1
        rows = rows[: MAX_LIST_LINES - 1]
        rows.append(("", f"… and {hidden} more", False))

    y = 1.0
    for prefix, text, heading in rows:
        if heading:
            ax.text(
                0.0,
                y,
                prefix,
                fontsize=8.4,
                color=MUTED,
                fontweight="bold",
                transform=ax.transAxes,
                va="top",
            )
        else:
            ax.text(0.0, y, prefix, fontsize=7.2, color=MUTED, transform=ax.transAxes, va="top")
            ax.text(
                0.10,
                y,
                wrap(text, 46),
                fontsize=7.2,
                color=INK,
                transform=ax.transAxes,
                va="top",
                linespacing=1.4,
            )
        y -= 1.0 / MAX_LIST_LINES


def _legend(fig) -> None:
    handles = [
        Line2D([], [], color=SCHEDULED, linewidth=5.5, label="Scheduled route"),
        Line2D([], [], color=DETOUR, linewidth=2.4, label="Road the detour takes"),
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
            label="Stop the detour skips",
        ),
        Line2D(
            [],
            [],
            marker="*",
            color="none",
            markerfacecolor=TEMPORARY,
            markeredgecolor="white",
            markersize=12,
            label="Stop served instead",
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
            label="Temporary stop (★)",
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
        columnspacing=1.6,
    )


def _headline(page: Detour) -> str:
    route = page.route.short_name or "?"
    span = f"[{page.start_sequence}..{page.end_sequence}]"
    skipped = _plural(len(page.skipped), "stop")
    served = len(page.replacement_stop_ids)

    if page.skipped and served:
        return f"Route {route}: {skipped} skipped over {span}, {served} served instead"
    if page.skipped:
        return f"Route {route}: {skipped} skipped over {span}, none replacing them"
    return f"Route {route}: {_plural(served, 'stop')} added at {span}, none skipped"


def draw_page(
    pdf: PdfPages, page: Detour, index: int, total: int, use_basemap: bool = True
) -> None:
    fig = plt.figure(figsize=PAGE_SIZE)
    fig.patch.set_facecolor("white")

    fig.text(0.055, 0.960, _headline(page), fontsize=14, color=INK, fontweight="bold", va="top")
    fig.text(0.055, 0.918, page.route.title(), fontsize=9.5, color=MUTED, va="top")

    fig.text(
        0.055,
        0.890,
        f"{page.entity_id}   ·   modification {page.modification_index}   ·   "
        f"{_plural(page.trips, 'trip')} over {_plural(page.dates, 'service date')}",
        fontsize=8,
        color=MUTED,
        va="top",
        family="monospace",
    )
    if page.also_patterns:
        count = len(page.also_patterns)
        fig.text(
            0.055,
            0.864,
            f"The same modification is written for {_plural(count, 'other route pattern')} "
            "of this line, with the stop sequences each one uses.",
            fontsize=8,
            color=MUTED,
            va="top",
        )

    bounds = _extent(page)
    _draw_map(fig.add_axes(list(MAP_BOX)), page, bounds, use_basemap)
    _draw_lists(fig.add_axes(list(LIST_BOX)), page)

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

    fig.text(0.055, 0.955, "STM detours", fontsize=23, color=INK, fontweight="bold", va="top")
    fig.text(
        0.055,
        0.905,
        "TripModifications built from the detours the STM publishes on its own website",
        fontsize=12.5,
        color=MUTED,
        va="top",
    )

    explanation = (
        "The line pages on stm.info mark every stop a detour skips and every stop it serves "
        "instead, and publish the road the vehicle leaves along with the road it takes. This "
        "feed is those flags, read against the trips running on the dates it covers, with no "
        "distance measured to decide what is served. Each page below draws one detour: the "
        "scheduled route in grey, the road the detour takes in teal, and the stops as the "
        "website marks them."
    )
    fig.text(
        0.055, 0.855, wrap(explanation, 118), fontsize=9.4, color=INK, va="top", linespacing=1.55
    )

    fig.text(0.055, 0.745, _facts(collected), fontsize=8.6, color=MUTED, va="top", linespacing=1.6)

    caveat = (
        "The website says nothing about how long a detour lasts, so every date the feed covers "
        "carries the same detours: one lifted tomorrow is still written for the rest of the "
        "week. A detour whose replacement stops the website does not list is written without "
        "them; web/report.md names those."
    )
    fig.text(0.055, 0.665, wrap(caveat, 118), fontsize=8.6, color=MUTED, va="top", linespacing=1.5)

    rows = list(enumerate(collected.pages[:COVER_ROWS], start=1))
    if rows:
        fig.text(0.055, 0.585, "Detours", fontsize=11, color=INK, fontweight="bold", va="top")
        _contents_table(fig.add_axes([0.055, 0.075, 0.89, 0.475]), rows, COVER_ROWS)

    fig.text(
        0.055,
        0.030,
        "Source data: Société de transport de Montréal, licensed CC-BY 4.0. "
        "This feed is unofficial and is not endorsed by the STM.",
        fontsize=7,
        color=MUTED,
    )
    pdf.savefig(fig)
    plt.close(fig)


def _facts(collected: Collected) -> str:
    metadata = collected.metadata
    dates = sorted(collected.service_dates)
    if len(dates) > 1:
        written = f"Service dates {dates[0]} to {dates[-1]}, {len(dates)} days"
    else:
        written = f"Service date {dates[0] if dates else 'unknown'}"
    lines = [written]

    if collected.feed_timestamp:
        moment = datetime.fromtimestamp(collected.feed_timestamp, UTC).astimezone(MONTREAL)
        lines[0] += f"   ·   feed timestamp {moment.strftime('%Y-%m-%d %H:%M')} America/Montreal"

    read = metadata.get("lines_read")
    detoured = metadata.get("lines_detoured")
    if read and detoured:
        lines.append(
            f"{read} line directions read from stm.info, {detoured} of them detoured, "
            f"in {metadata.get('requests_made', 'unknown')} requests"
        )

    lines.append(
        f"{collected.entities} entities   ·   {collected.modifications} modifications   ·   "
        f"{collected.temporary_stops} temporary stops   ·   {len(collected.pages)} drawn below"
    )
    if collected.undrawable:
        lines.append(
            f"{collected.undrawable} modifications name a trip the static feed no longer has "
            "and are not drawn"
        )
    return "\n".join(lines)


def _contents_table(ax, rows, capacity: int) -> None:
    """One block of the contents: which route, which span, and which page."""
    ax.axis("off")

    columns = [0.0, 0.06, 0.36, 0.45, 0.52, 0.60]
    headers = ["Route", "Direction", "Span", "Skips", "Serves", "Page"]
    for x, header in zip(columns, headers, strict=True):
        ax.text(
            x,
            1.0,
            header,
            fontsize=8,
            color=MUTED,
            fontweight="bold",
            transform=ax.transAxes,
            va="top",
        )
    ax.axhline(y=0.985, xmin=0, xmax=0.66, color="#d8d8d8", linewidth=0.8)

    row_height = 0.96 / (capacity + 1)
    y = 0.975
    for index, page in rows:
        y -= row_height
        cells = [
            page.route.short_name or "?",
            (page.route.headsign or page.route.long_name)[:40],
            f"[{page.start_sequence}..{page.end_sequence}]",
            str(len(page.skipped)),
            str(len(page.replacement_stop_ids)),
            str(index),
        ]
        for column, (x, cell) in enumerate(zip(columns, cells, strict=True)):
            ax.text(
                x,
                y,
                cell,
                fontsize=7.4,
                color=INK,
                transform=ax.transAxes,
                va="top",
                family="monospace" if column != 1 else None,
            )


def draw_contents(pdf: PdfPages, collected: Collected) -> int:
    """The rest of the contents, over as many pages as it takes."""
    rest = collected.pages[COVER_ROWS:]
    chunks = [rest[start : start + CONTENTS_ROWS] for start in range(0, len(rest), CONTENTS_ROWS)]

    for number, chunk in enumerate(chunks):
        first = COVER_ROWS + number * CONTENTS_ROWS + 1
        fig = plt.figure(figsize=PAGE_SIZE)
        fig.patch.set_facecolor("white")
        fig.text(
            0.055, 0.945, "Detours, continued", fontsize=15, color=INK, fontweight="bold", va="top"
        )
        _contents_table(
            fig.add_axes([0.055, 0.06, 0.89, 0.845]),
            list(enumerate(chunk, start=first)),
            CONTENTS_ROWS,
        )
        pdf.savefig(fig)
        plt.close(fig)

    return len(chunks)


def build_report(
    collected: Collected,
    output_path,
    use_basemap: bool = True,
    tile_cache_dir: str = ".tilecache",
) -> int:
    """Write the PDF and return its page count."""
    if use_basemap:
        set_tile_cache(tile_cache_dir)

    with PdfPages(output_path) as pdf:
        draw_cover(pdf, collected)
        front = 1 + draw_contents(pdf, collected)
        for index, page in enumerate(collected.pages, start=1):
            draw_page(pdf, page, index, len(collected.pages), use_basemap)
        info = pdf.infodict()
        info["Title"] = "STM detours, built from the STM website"
        info["Subject"] = f"Service dates {', '.join(sorted(collected.service_dates))}"
        info["Creator"] = "stm-tripmodifications-fix"
    return front + len(collected.pages)
