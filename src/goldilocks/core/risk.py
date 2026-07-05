"""RiskManager: the single shared sizing + risk component (roadmap W1).

Both the backtest engine and the live engine MUST route every signal through
size_signal() and every order through check_order() — never their own copies — so the
rules a backtest validates are exactly the rules live trading enforces. Strategies are
assumed to be buggy; limits here are enforcement, not advice.

Rules, in check order:
1. KILL_SWITCH file — blocks everything except backtests, including exits.
2. Daily drawdown halt — blocks risk-increasing orders for the rest of the UTC day;
   orders that only reduce an existing position stay allowed.
3. Max position size — the resulting position's notional may not exceed
   allocation * max_position_pct / 100.
4. Capital allocation — the resulting position's notional may not exceed allocation.
   (v1 checks per-instrument; cross-instrument exposure is roadmap phase 7.)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from goldilocks.core.portfolio import Portfolio
from goldilocks.core.types import Order, OrderSide, Signal, TradingMode


@dataclass(frozen=True)
class RiskLimits:
    allocation: Decimal               # max capital this strategy instance may deploy
    max_position_pct: Decimal         # max % of allocation in a single position
    daily_drawdown_halt_pct: Decimal  # halt strategy after losing this % in a day


@dataclass(frozen=True)
class RiskVerdict:
    approved: bool
    reason: str = ""


class RiskManager:
    def __init__(self, limits: RiskLimits, kill_switch_file: Path) -> None:
        self.limits = limits
        self.kill_switch_file = kill_switch_file
        self._halted = False
        self._day: date | None = None
        self._day_start_equity: Decimal | None = None

    # --- sizing -----------------------------------------------------------------

    def size_signal(self, signal: Signal, portfolio: Portfolio, price: Decimal) -> Decimal:
        """Turn a strategy's intent into a quantity. 0 means nothing to do.

        Exits size to the full open position. Entries scale the per-position cap by
        the signal's strength; a same-direction position is never added to (v1).
        """
        pos = portfolio.position(signal.instrument)
        if signal.exit:
            return abs(pos.quantity) if pos else Decimal(0)
        if pos is not None:
            same_direction = (pos.quantity > 0) == (signal.side is OrderSide.BUY)
            if same_direction:
                return Decimal(0)
        max_notional = self.limits.allocation * self.limits.max_position_pct / 100
        # Forex quantities are whole base-currency units.
        return Decimal(int(max_notional * signal.strength / price))

    # --- daily drawdown tracking --------------------------------------------------

    def record_equity(self, timestamp: datetime, equity: Decimal) -> None:
        """Feed one equity observation. The engine calls this every bar/tick; the halt
        latches for the rest of the UTC day and resets on the first observation of the
        next day."""
        day = timestamp.date()
        if day != self._day:
            self._day = day
            self._day_start_equity = equity
            self._halted = False
            return
        if self._halted or not self._day_start_equity or self._day_start_equity <= 0:
            return
        loss_pct = (self._day_start_equity - equity) / self._day_start_equity * 100
        if loss_pct >= self.limits.daily_drawdown_halt_pct:
            self._halted = True

    @property
    def halted(self) -> bool:
        return self._halted

    def restore_day_state(
        self, day: date, day_start_equity: Decimal | None, halted: bool
    ) -> None:
        """Re-arm today's drawdown state after an engine restart (W5). Without this,
        stop/start would reset the day baseline and un-latch an active halt."""
        self._day = day
        self._day_start_equity = day_start_equity
        self._halted = halted

    # --- the gate -----------------------------------------------------------------

    def check_order(self, order: Order, portfolio: Portfolio, price: Decimal) -> RiskVerdict:
        """The last gate before any order leaves the engine. `price` is the current
        mark used for notional math (market orders have no price of their own)."""
        if order.mode is not TradingMode.BACKTEST and self.kill_switch_file.exists():
            return RiskVerdict(False, "KILL_SWITCH file present — all trading halted")

        pos = portfolio.position(order.instrument)
        held = pos.quantity if pos else Decimal(0)
        signed = order.quantity if order.side is OrderSide.BUY else -order.quantity
        resulting = held + signed

        reduces_only = abs(resulting) < abs(held) and (resulting == 0 or (resulting > 0) == (held > 0))
        if reduces_only:
            return RiskVerdict(True)

        if self._halted:
            return RiskVerdict(
                False,
                f"daily drawdown halt active ({self.limits.daily_drawdown_halt_pct}% "
                f"lost since day start) — only position-reducing orders allowed",
            )

        resulting_notional = abs(resulting) * price
        max_position = self.limits.allocation * self.limits.max_position_pct / 100
        if resulting_notional > max_position:
            return RiskVerdict(
                False,
                f"position notional {resulting_notional} would exceed max position "
                f"{max_position} ({self.limits.max_position_pct}% of allocation)",
            )
        if resulting_notional > self.limits.allocation:
            return RiskVerdict(
                False,
                f"position notional {resulting_notional} would exceed allocation "
                f"{self.limits.allocation}",
            )
        return RiskVerdict(True)
