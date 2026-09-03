"""Map report for the feed built from the STM website.

The repair report shows what the STM's own feed got wrong. This one has no
wrong version to show: it draws each detour the website publishes, so the
stops it says are skipped can be checked against the road the vehicle takes.
"""

from __future__ import annotations

__all__ = ["build_report"]


def build_report(*args, **kwargs):
    """Build the PDF. Imported lazily so nothing else pulls in matplotlib."""
    from .render import build_report as _build_report

    return _build_report(*args, **kwargs)
