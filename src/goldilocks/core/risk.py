"""RiskManager: the last gate before any order leaves the engine.

Every order — backtest, paper, shadow, or live — passes through check_order(). Strategies
are assumed to be buggy; limits here are enforcement, not advice. Roadmap phase 2 wires
this into the engine loop.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from goldilocks.core import Order, TradingMode


@dataclass(frozen=True)
class RiskLimits:
    allocation: Decimal              # max capital this strategy instance may deploy
    max_position_pct: Decimal        # max % of allocation in a single position
    daily_drawdown_halt_pct: Decimal # halt strategy after losing this % in a day


@dataclass(frozen=True)
class RiskVerdict:
    approved: bool
    reason: str = ""


class RiskManager:
    def __init__(self, limits: RiskLimits, kill_switch_file: Path) -> None:
        self.limits = limits
        self.kill_switch_file = kill_switch_file
        self._halted = False

    def check_order(self, order: Order) -> RiskVerdict:
        """Checks, in order:
        1. KILL_SWITCH file — blocks everything except backtests.
        2. Daily drawdown halt already triggered for this strategy.
        3. Order would exceed the strategy's capital allocation.
        4. Order would exceed max position size.
        Implementation lands in roadmap phase 2 (needs the portfolio/state store for 2-4).
        """
        if order.mode is not TradingMode.BACKTEST and self.kill_switch_file.exists():
            return RiskVerdict(False, "KILL_SWITCH file present — all trading halted")
        raise NotImplementedError("roadmap phase 2")
