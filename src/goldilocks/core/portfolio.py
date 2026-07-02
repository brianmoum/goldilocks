"""Per-strategy portfolio accounting: positions, cash used vs allocation, realized and
unrealized P&L, daily drawdown tracking (feeds RiskManager). Roadmap phase 2, shared with
the backtest engine in phase 1 so accounting is identical in both."""

from __future__ import annotations


class Portfolio:
    def __init__(self) -> None:
        raise NotImplementedError("roadmap phases 1-2")
