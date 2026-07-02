"""OANDA v20 connector — forex. Roadmap phase 2 (data adapter in phase 1).

Practice and live are different hosts AND different tokens:
  practice: https://api-fxpractice.oanda.com   (OANDA_PRACTICE_API_TOKEN / _ACCOUNT_ID)
  live:     https://api-fxtrade.oanda.com      (OANDA_LIVE_API_TOKEN / _ACCOUNT_ID)
Streaming hosts: stream-fxpractice / stream-fxtrade. Never mix the two sets.

Notes for implementation:
- Plain REST + HTTP streaming via httpx; no SDK needed.
- Quantities are base-currency units (signed for direction), not lots.
- Candles endpoint: /v3/instruments/{instrument}/candles (granularity=M15 etc.).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from goldilocks.connectors.base import BrokerConnector
from goldilocks.core import AssetClass, Bar, Fill, Order, Position, TradingMode
from goldilocks.core.types import AccountSnapshot

_HOSTS = {
    TradingMode.PAPER: "https://api-fxpractice.oanda.com",
    TradingMode.SHADOW: "https://api-fxpractice.oanda.com",
    TradingMode.LIVE: "https://api-fxtrade.oanda.com",
}


class OandaConnector(BrokerConnector):
    supports = {AssetClass.FOREX}

    def __init__(self, mode: TradingMode) -> None:
        super().__init__(mode)
        self.host = _HOSTS[mode]

    async def connect(self) -> None:
        raise NotImplementedError("roadmap phase 2")

    async def get_account(self) -> AccountSnapshot:
        raise NotImplementedError("roadmap phase 2")

    async def get_positions(self) -> list[Position]:
        raise NotImplementedError("roadmap phase 2")

    async def submit_order(self, order: Order) -> str:
        raise NotImplementedError("roadmap phase 2")

    async def cancel_order(self, broker_order_id: str) -> None:
        raise NotImplementedError("roadmap phase 2")

    def stream_bars(self, instruments: list[str], timeframe: str) -> AsyncIterator[Bar]:
        raise NotImplementedError("roadmap phase 2")

    def stream_fills(self) -> AsyncIterator[Fill]:
        raise NotImplementedError("roadmap phase 2")
