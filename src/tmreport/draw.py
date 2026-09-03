"""The drawing both map reports are made of.

Neither report cares what a Web Mercator metre is or how a scale bar is
placed, and both need the same answers, so the page furniture lives here and
each report keeps only what it has to say.
"""

from __future__ import annotations

import logging
import math
import textwrap
from collections.abc import Iterable, Sequence
from zoneinfo import ZoneInfo

import contextily as cx
import matplotlib
import matplotlib.patheffects as path_effects

matplotlib.use("Agg")

from tmfix.geometry import LatLon

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

Bounds = tuple[float, float, float, float]


def to_mercator(point: LatLon) -> tuple[float, float]:
    """WGS84 to Web Mercator metres, which is what the tiles are drawn in."""
    x = math.radians(point.lon) * EARTH_RADIUS_M
    y = math.log(math.tan(math.pi / 4 + math.radians(point.lat) / 2)) * EARTH_RADIUS_M
    return x, y


def mercator_scale(latitude: float) -> float:
    """Metres on the ground per Mercator metre at a latitude."""
    return math.cos(math.radians(latitude))


def clip(line: Sequence[LatLon], bounds: Bounds):
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


def fit_bounds(
    points: Sequence[LatLon],
    nearby: Iterable[Sequence[LatLon]] = (),
    aspect: float = 1.05,
) -> Bounds:
    """A view holding every point, plus the lines in `nearby` where they pass close.

    The window the lines are read against is fixed before the loop: growing it
    as points are accepted would let one distant vertex drag in the whole route.
    """
    xs, ys = zip(*(to_mercator(point) for point in points), strict=True)
    left, right, bottom, top = min(xs), max(xs), min(ys), max(ys)

    scale = mercator_scale(points[0].lat)
    margin = 0.35 * max(right - left, top - bottom, MIN_SPAN_M / scale)
    window = (left - margin, bottom - margin, right + margin, top + margin)
    for line in nearby:
        for x, y in (to_mercator(point) for point in line):
            if window[0] <= x <= window[2] and window[1] <= y <= window[3]:
                left, right = min(left, x), max(right, x)
                bottom, top = min(bottom, y), max(top, y)

    width = max(right - left, MIN_SPAN_M / scale)
    height = max(top - bottom, MIN_SPAN_M / scale)
    # Match the panel's aspect so neither axis is stretched.
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


def zoom_for(bounds: Bounds, panel_pixels: int = 1100) -> int:
    """Tile zoom that fills the panel, clamped to what the provider serves."""
    left, _, right, _ = bounds
    world = 2 * math.pi * EARTH_RADIUS_M
    zoom = math.log2(world / (right - left) * panel_pixels / 256)
    return max(1, min(BASEMAP_MAX_ZOOM, round(zoom)))


def frame(ax, bounds: Bounds) -> None:
    """A panel with no ticks and a hairline border, set to the view."""
    left, bottom, right, top = bounds
    ax.set_xlim(left, right)
    ax.set_ylim(bottom, top)
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_edgecolor("#c9c9c9")
        spine.set_linewidth(0.8)


def basemap(ax, bounds: Bounds, use_basemap: bool = True, panel_pixels: int = 1100) -> None:
    """Street tiles under the panel, or a flat ground when they cannot be had."""
    if use_basemap:
        try:
            cx.add_basemap(
                ax,
                source=cx.providers.Esri.WorldGrayCanvas,
                attribution=False,
                zoom=zoom_for(bounds, panel_pixels),
                headers={"User-Agent": USER_AGENT},
            )
            return
        except Exception as error:  # a map without streets is poor, but better than no page
            logging.warning("basemap tiles unavailable: %s", error)
    ax.set_facecolor("#f2f2f0")


def scale_bar(ax, bounds: Bounds, latitude: float) -> None:
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


def place_labels(ax, labelled, bounds: Bounds) -> None:
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


def wrap(text: str, width: int) -> str:
    return "\n".join(textwrap.wrap(text, width))


def set_tile_cache(tile_cache_dir: str) -> None:
    """Point contextily at a cache directory and name us in its requests."""
    cx.set_cache_dir(tile_cache_dir)
    cx.tile.USER_AGENT = USER_AGENT
