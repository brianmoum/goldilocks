"""Shared monitor helpers. Every monitor surface (CLI table, TUI, web) reads ONLY the
SQLite state store — never brokers — so they all show the same truth (CLAUDE.md
convention). The heavy lifting lives in StateStore.status_rows(); this module holds
presentation helpers shared across surfaces.
"""

from __future__ import annotations

from decimal import Decimal

_BLOCKS = "▁▂▃▄▅▆▇█"


def sparkline(values: list[Decimal], width: int = 24) -> str:
    """Render an equity series as a unicode sparkline, downsampled to `width`."""
    if not values:
        return ""
    if len(values) > width:
        step = len(values) / width
        values = [values[int(i * step)] for i in range(width)]
    lo, hi = min(values), max(values)
    if hi == lo:
        return _BLOCKS[0] * len(values)
    span = hi - lo
    return "".join(
        _BLOCKS[min(len(_BLOCKS) - 1, int((v - lo) / span * len(_BLOCKS)))]
        for v in values
    )
