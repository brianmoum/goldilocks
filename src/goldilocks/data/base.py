"""Historical market data adapters — separate from execution connectors so backtests
never need broker credentials beyond a data key, and data sources can differ from the
executing broker.

First implementation (roadmap phase 1): OANDA candles for forex, cached as parquet or
CSV under data/cache/ keyed by (instrument, timeframe, date range).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from goldilocks.core import Bar


class DataFeed(ABC):
    @abstractmethod
    def get_bars(
        self, instrument: str, timeframe: str, start: datetime, end: datetime
    ) -> list[Bar]:
        """Return completed historical bars, using the local cache when possible."""
