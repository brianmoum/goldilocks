"""Backtest engine: replays historical bars through the identical Strategy interface.

Roadmap phase 1. Design constraints:
- Reuse Portfolio for accounting so backtest and live P&L math cannot diverge.
- Fill simulation v1: fill at NEXT bar's open (never the signal bar's close — that's
  lookahead), apply bid/ask spread. Slippage model calibrated later (phase 6).
- Feed warmup_bars() silently before scoring begins.
- Output: total return, max drawdown, win rate, profit factor, trade list.
- Walk-forward validation becomes the default mode in phase 7.
"""

from __future__ import annotations


class BacktestEngine:
    def __init__(self) -> None:
        raise NotImplementedError("roadmap phase 1")
