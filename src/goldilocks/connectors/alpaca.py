"""Alpaca connector — equities, crypto, and (later) options. Roadmap phase 4.

Uses the alpaca-py SDK (install extra: `pip install -e ".[alpaca]"`). Paper vs live is
selected by key pair (ALPACA_PAPER_* vs ALPACA_LIVE_*) and the paper=True/False client
flag — same rule as OANDA: one connector instance, one mode, forever.

Equities gotchas for implementation: market sessions and halts (the engine must not
expect bars 24/7), and PDT rules on accounts under $25k.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from goldilocks.connectors.base import BrokerConnector
from goldilocks.core import AssetClass, Bar, Fill, Order, Position
from goldilocks.core.types import AccountSnapshot


class AlpacaConnector(BrokerConnector):
    supports = {AssetClass.EQUITIES, AssetClass.CRYPTO}  # + OPTIONS in roadmap phase 5

    async def connect(self) -> None:
        raise NotImplementedError("roadmap phase 4")

    async def get_account(self) -> AccountSnapshot:
        raise NotImplementedError("roadmap phase 4")

    async def get_positions(self) -> list[Position]:
        raise NotImplementedError("roadmap phase 4")

    async def submit_order(self, order: Order) -> str:
        raise NotImplementedError("roadmap phase 4")

    async def cancel_order(self, broker_order_id: str) -> None:
        raise NotImplementedError("roadmap phase 4")

    def stream_bars(self, instruments: list[str], timeframe: str) -> AsyncIterator[Bar]:
        raise NotImplementedError("roadmap phase 4")

    def stream_fills(self) -> AsyncIterator[Fill]:
        raise NotImplementedError("roadmap phase 4")
