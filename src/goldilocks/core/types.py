"""Core domain types shared by strategies, the engine, connectors, and the backtester.

Money and quantities are Decimal, never float (see CLAUDE.md). Timestamps are
timezone-aware UTC.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal


class AssetClass(enum.StrEnum):
    FOREX = "forex"
    CRYPTO = "crypto"
    EQUITIES = "equities"
    OPTIONS = "options"


class TradingMode(enum.StrEnum):
    BACKTEST = "backtest"
    PAPER = "paper"
    # Shadow: live data and real signal generation, but orders are logged, not sent.
    # The calibration step between paper and live (roadmap phase 6).
    SHADOW = "shadow"
    LIVE = "live"


class OrderSide(enum.StrEnum):
    BUY = "buy"
    SELL = "sell"


@dataclass(frozen=True, slots=True)
class Bar:
    instrument: str
    timestamp: datetime          # bar open time, UTC
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    timeframe: str               # e.g. "M15", "H1", "D"


@dataclass(frozen=True, slots=True)
class Signal:
    """What a strategy emits. Intent only — sizing beyond `strength` and all routing
    is the engine's job, subject to the RiskManager."""

    instrument: str
    side: OrderSide
    strength: Decimal = Decimal(1)   # 0..1 fraction of the strategy's allowed position size
    reason: str = ""                 # human-readable, shows up in the monitor and logs
    exit: bool = False               # True = close/reduce existing position, not open new


@dataclass(frozen=True, slots=True)
class Order:
    order_id: str
    strategy_name: str
    instrument: str
    side: OrderSide
    quantity: Decimal
    mode: TradingMode
    created_at: datetime
    limit_price: Decimal | None = None   # None = market order


@dataclass(frozen=True, slots=True)
class Fill:
    order_id: str
    instrument: str
    side: OrderSide
    quantity: Decimal
    price: Decimal
    filled_at: datetime
    commission: Decimal = Decimal(0)


@dataclass(slots=True)
class Position:
    instrument: str
    quantity: Decimal            # signed: negative = short
    avg_entry_price: Decimal
    unrealized_pnl: Decimal = Decimal(0)


@dataclass(slots=True)
class AccountSnapshot:
    balance: Decimal
    currency: str
    positions: list[Position] = field(default_factory=list)
    taken_at: datetime | None = None
