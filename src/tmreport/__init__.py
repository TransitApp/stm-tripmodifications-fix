"""Before/after map report for the repairs `tmfix` makes.

Kept apart from `tmfix` because drawing needs matplotlib, contextily and a
third-party tile server, none of which the ten-minute repair path should carry.
Install it with `pip install -e ".[report]"`.
"""

from __future__ import annotations

__all__ = ["build_report"]


def build_report(*args, **kwargs):
    """Build the PDF. Imported lazily so `tmfix` never pulls in matplotlib."""
    from .render import build_report as _build_report

    return _build_report(*args, **kwargs)
