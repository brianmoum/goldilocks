"""Broker connector abstraction.

One implementation per broker; the engine picks a connector by asset class and config.
Connectors are the ONLY code allowed to talk to broker APIs, and they must be constructed
with an explicit TradingMode — a connector instance is permanently paper OR live, never
switchable after construction, so credentials can't cross over.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import ClassVar

from goldilocks.core import AssetClass, Bar, Fill, Order, Position, TradingMode
from goldilocks.core.types import AccountSnapshot


class BrokerConnector(ABC):
    supports: ClassVar[set[AssetClass]]

    def __init__(self, mode: TradingMode) -> None:
        if mode is TradingMode.BACKTEST:
            raise ValueError("backtests use the simulated broker, not a connector")
        self.mode = mode

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def get_account(self) -> AccountSnapshot: ...

    @abstractmethod
    async def get_positions(self) -> list[Position]: ...

    @abstractmethod
    async def submit_order(self, order: Order) -> str:
        """Submit an order; return the broker's order id."""

    @abstractmethod
    async def cancel_order(self, broker_order_id: str) -> None: ...

    @abstractmethod
    def stream_bars(self, instruments: list[str], timeframe: str) -> AsyncIterator[Bar]:
        """Yield completed bars for the given instruments as they close."""

    @abstractmethod
    def stream_fills(self) -> AsyncIterator[Fill]:
        """Yield fills for this account's orders as they happen."""
