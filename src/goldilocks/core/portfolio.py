"""Per-strategy portfolio accounting: positions, cash used vs allocation, realized and
unrealized P&L, daily drawdown tracking (feeds RiskManager). Shared by the backtest engine
(phase 1) and the live engine (phase 2) so accounting is identical in both.

Cash accounting is notional: a buy debits quantity * price, a sell credits it, so
equity = cash + sum(position quantity * mark price). Margin modelling comes later.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from goldilocks.core.types import Fill, OrderSide, Position


@dataclass(frozen=True, slots=True)
class Trade:
    """A closed round trip (or the closed part of one, on partial exits)."""

    instrument: str
    side: OrderSide              # direction of the entry
    quantity: Decimal            # always positive
    entry_price: Decimal         # average entry of the closed quantity
    exit_price: Decimal
    opened_at: datetime
    closed_at: datetime
    pnl: Decimal                 # realized, net of the exit fill's commission


@dataclass(slots=True)
class Portfolio:
    initial_cash: Decimal
    cash: Decimal = field(init=False)
    realized_pnl: Decimal = field(init=False, default=Decimal(0))
    closed_trades: list[Trade] = field(init=False, default_factory=list)
    _positions: dict[str, Position] = field(init=False, default_factory=dict)
    _opened_at: dict[str, datetime] = field(init=False, default_factory=dict)

    def __post_init__(self) -> None:
        self.cash = self.initial_cash

    def position(self, instrument: str) -> Position | None:
        return self._positions.get(instrument)

    @property
    def positions(self) -> list[Position]:
        return list(self._positions.values())

    def apply_fill(self, fill: Fill) -> None:
        signed = fill.quantity if fill.side is OrderSide.BUY else -fill.quantity
        self.cash -= signed * fill.price + fill.commission

        pos = self._positions.get(fill.instrument)
        held = pos.quantity if pos else Decimal(0)

        if held == 0 or (held > 0) == (signed > 0):
            # Opening or adding: weighted-average entry price.
            if pos is None:
                self._positions[fill.instrument] = Position(
                    fill.instrument, signed, fill.price
                )
                self._opened_at[fill.instrument] = fill.filled_at
            else:
                total = held + signed
                pos.avg_entry_price = (
                    held * pos.avg_entry_price + signed * fill.price
                ) / total
                pos.quantity = total
            return

        # Reducing (or flipping through) an existing position.
        assert pos is not None
        closed_qty = min(abs(signed), abs(held))
        direction = Decimal(1) if held > 0 else Decimal(-1)
        pnl = (fill.price - pos.avg_entry_price) * closed_qty * direction
        pnl -= fill.commission
        self.realized_pnl += pnl
        self.closed_trades.append(
            Trade(
                instrument=fill.instrument,
                side=OrderSide.BUY if held > 0 else OrderSide.SELL,
                quantity=closed_qty,
                entry_price=pos.avg_entry_price,
                exit_price=fill.price,
                opened_at=self._opened_at[fill.instrument],
                closed_at=fill.filled_at,
                pnl=pnl,
            )
        )

        remainder = held + signed
        if remainder == 0:
            del self._positions[fill.instrument]
            del self._opened_at[fill.instrument]
        elif (remainder > 0) != (held > 0):
            # Flipped through zero: the excess opens a new position at the fill price.
            pos.quantity = remainder
            pos.avg_entry_price = fill.price
            self._opened_at[fill.instrument] = fill.filled_at
        else:
            pos.quantity = remainder

    def unrealized_pnl(self, prices: dict[str, Decimal]) -> Decimal:
        total = Decimal(0)
        for pos in self._positions.values():
            mark = prices[pos.instrument]
            total += (mark - pos.avg_entry_price) * pos.quantity
        return total

    def equity(self, prices: dict[str, Decimal]) -> Decimal:
        total = self.cash
        for pos in self._positions.values():
            total += pos.quantity * prices[pos.instrument]
        return total
