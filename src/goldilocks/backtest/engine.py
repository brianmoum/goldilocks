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

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from goldilocks.core import Bar, Fill, Order, OrderSide, Position, Signal, TradingMode
from goldilocks.core.portfolio import Portfolio, Trade
from goldilocks.strategies.base import Strategy


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    allocation: Decimal              # capital the strategy may deploy
    max_position_pct: Decimal        # max % of allocation in one position
    spread: Decimal = Decimal(0)     # full bid/ask spread in price units (mid candles)


@dataclass(slots=True)
class BacktestResult:
    strategy_name: str
    instrument: str
    timeframe: str
    start: datetime
    end: datetime
    bars: int
    initial_equity: Decimal
    final_equity: Decimal
    total_return_pct: Decimal
    max_drawdown_pct: Decimal
    trades: list[Trade]
    open_position: Position | None
    equity_curve: list[tuple[datetime, Decimal]] = field(repr=False, default_factory=list)

    @property
    def win_rate_pct(self) -> Decimal | None:
        if not self.trades:
            return None
        wins = sum(1 for t in self.trades if t.pnl > 0)
        return Decimal(wins) / len(self.trades) * 100

    @property
    def profit_factor(self) -> Decimal | None:
        gross_profit = sum((t.pnl for t in self.trades if t.pnl > 0), Decimal(0))
        gross_loss = sum((-t.pnl for t in self.trades if t.pnl < 0), Decimal(0))
        if gross_loss == 0:
            return None if gross_profit == 0 else Decimal("Infinity")
        return gross_profit / gross_loss

    def report(self) -> str:
        def pct(v: Decimal | None) -> str:
            return "n/a" if v is None else f"{v:.2f}%"

        pf = self.profit_factor
        lines = [
            f"Backtest: {self.strategy_name} on {self.instrument} ({self.timeframe})",
            f"Period:   {self.start:%Y-%m-%d} .. {self.end:%Y-%m-%d}  ({self.bars} bars)",
            "",
            f"Initial equity:  {self.initial_equity:>14.2f}",
            f"Final equity:    {self.final_equity:>14.2f}",
            f"Total return:    {pct(self.total_return_pct):>14}",
            f"Max drawdown:    {pct(self.max_drawdown_pct):>14}",
            f"Trades (closed): {len(self.trades):>14}",
            f"Win rate:        {pct(self.win_rate_pct):>14}",
            f"Profit factor:   {'n/a' if pf is None else f'{pf:.2f}':>14}",
        ]
        if self.open_position is not None:
            p = self.open_position
            lines.append(
                f"Open position:   {p.quantity} {p.instrument} @ {p.avg_entry_price}"
            )
        if self.trades:
            lines += ["", "Trade list:"]
            lines.append(
                f"{'#':>3}  {'opened (UTC)':16}  {'closed (UTC)':16}  {'side':4}  "
                f"{'qty':>8}  {'entry':>9}  {'exit':>9}  {'P&L':>10}"
            )
            for i, t in enumerate(self.trades, 1):
                lines.append(
                    f"{i:>3}  {t.opened_at:%Y-%m-%d %H:%M}  {t.closed_at:%Y-%m-%d %H:%M}  "
                    f"{t.side.value:4}  {t.quantity:>8}  {t.entry_price:>9.5f}  "
                    f"{t.exit_price:>9.5f}  {t.pnl:>10.2f}"
                )
        return "\n".join(lines)


class BacktestEngine:
    def __init__(self, strategy: Strategy, config: BacktestConfig) -> None:
        self.strategy = strategy
        self.config = config
        self.portfolio = Portfolio(initial_cash=config.allocation)

    def run(self, bars: list[Bar]) -> BacktestResult:
        if not bars:
            raise ValueError("no bars to backtest")
        warmup = self.strategy.warmup_bars()
        pending: list[Signal] = []
        equity_curve: list[tuple[datetime, Decimal]] = []

        for i, bar in enumerate(bars):
            # Signals queued on the previous bar fill at this bar's open, spread applied.
            for signal in pending:
                self._execute(signal, bar)
            pending = []

            signals = self.strategy.on_bar(bar)
            if i >= warmup:
                pending = signals

            equity_curve.append(
                (bar.timestamp, self.portfolio.equity({bar.instrument: bar.close}))
            )

        final_equity = equity_curve[-1][1]
        instrument = bars[0].instrument
        return BacktestResult(
            strategy_name=self.strategy.name,
            instrument=instrument,
            timeframe=bars[0].timeframe,
            start=bars[0].timestamp,
            end=bars[-1].timestamp,
            bars=len(bars),
            initial_equity=self.config.allocation,
            final_equity=final_equity,
            total_return_pct=(final_equity - self.config.allocation)
            / self.config.allocation
            * 100,
            max_drawdown_pct=_max_drawdown_pct(equity_curve),
            trades=list(self.portfolio.closed_trades),
            open_position=self.portfolio.position(instrument),
            equity_curve=equity_curve,
        )

    def _execute(self, signal: Signal, bar: Bar) -> None:
        half_spread = self.config.spread / 2
        price = (
            bar.open + half_spread
            if signal.side is OrderSide.BUY
            else bar.open - half_spread
        )
        quantity = self._size(signal, price)
        if quantity <= 0:
            return
        order = Order(
            order_id=uuid.uuid4().hex,
            strategy_name=self.strategy.name,
            instrument=signal.instrument,
            side=signal.side,
            quantity=quantity,
            mode=TradingMode.BACKTEST,
            created_at=bar.timestamp,
        )
        fill = Fill(
            order_id=order.order_id,
            instrument=order.instrument,
            side=order.side,
            quantity=order.quantity,
            price=price,
            filled_at=bar.timestamp,
        )
        self.portfolio.apply_fill(fill)
        self.strategy.on_fill(fill)

    def _size(self, signal: Signal, price: Decimal) -> Decimal:
        pos = self.portfolio.position(signal.instrument)
        if signal.exit:
            return abs(pos.quantity) if pos else Decimal(0)
        # Sizing mirrors what the live engine will do: strength scales the per-position
        # cap; never add to an existing same-direction position (v1).
        if pos is not None:
            same_direction = (pos.quantity > 0) == (signal.side is OrderSide.BUY)
            if same_direction:
                return Decimal(0)
        max_notional = self.config.allocation * self.config.max_position_pct / 100
        # Forex quantities are whole base-currency units.
        return Decimal(int(max_notional * signal.strength / price))


def _max_drawdown_pct(equity_curve: list[tuple[datetime, Decimal]]) -> Decimal:
    peak = equity_curve[0][1]
    worst = Decimal(0)
    for _, equity in equity_curve:
        peak = max(peak, equity)
        if peak > 0:
            drawdown = (peak - equity) / peak * 100
            worst = max(worst, drawdown)
    return worst
