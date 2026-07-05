"""OANDA historical candles adapter (roadmap phase 1).

Always talks to the PRACTICE host — historical candle data is identical on practice and
live, so backtests never need (or touch) live credentials. Mid-price candles; the
backtester applies the bid/ask spread at fill time.

Fetched ranges are cached as CSV under data/cache/ keyed by (instrument, timeframe,
start, end), so repeat backtests are offline.
"""

from __future__ import annotations

import csv
import os
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx

from goldilocks.core import Bar
from goldilocks.data.base import DataFeed

_PRACTICE_HOST = "https://api-fxpractice.oanda.com"
_MAX_CANDLES_PER_REQUEST = 5000

# Granularity -> bar length, for advancing the pagination cursor.
_GRANULARITY_SECONDS = {
    "S5": 5, "S10": 10, "S15": 15, "S30": 30,
    "M1": 60, "M2": 120, "M4": 240, "M5": 300, "M10": 600, "M15": 900, "M30": 1800,
    "H1": 3600, "H2": 7200, "H3": 10800, "H4": 14400, "H6": 21600, "H8": 28800,
    "H12": 43200, "D": 86400, "W": 604800,
}


class MissingTokenError(RuntimeError):
    pass


def _parse_oanda_time(value: str) -> datetime:
    """OANDA returns RFC3339 with nanosecond precision; trim to microseconds."""
    value = value.replace("Z", "+00:00")
    if "." in value:
        head, rest = value.split(".", 1)
        frac, offset = rest[:-6], rest[-6:]
        value = f"{head}.{frac[:6]}{offset}"
    return datetime.fromisoformat(value)


class OandaDataFeed(DataFeed):
    def __init__(
        self,
        api_token: str | None = None,
        cache_dir: Path = Path("data/cache"),
        client: httpx.Client | None = None,
        use_cache: bool = True,
    ) -> None:
        self._api_token = api_token
        self.cache_dir = cache_dir
        self._client = client
        self.use_cache = use_cache

    def get_bars(
        self, instrument: str, timeframe: str, start: datetime, end: datetime
    ) -> list[Bar]:
        if timeframe not in _GRANULARITY_SECONDS:
            raise ValueError(f"unknown OANDA granularity: {timeframe!r}")
        if not self.use_cache:
            return self._fetch(instrument, timeframe, start, end)
        cache_file = self._cache_path(instrument, timeframe, start, end)
        if cache_file.exists():
            return self._read_cache(cache_file, instrument, timeframe)
        bars = self._fetch(instrument, timeframe, start, end)
        self._write_cache(cache_file, bars)
        return bars

    # --- cache ---

    def _cache_path(
        self, instrument: str, timeframe: str, start: datetime, end: datetime
    ) -> Path:
        key = f"{instrument}_{timeframe}_{start:%Y%m%dT%H%M%S}_{end:%Y%m%dT%H%M%S}.csv"
        return self.cache_dir / key

    @staticmethod
    def _read_cache(path: Path, instrument: str, timeframe: str) -> list[Bar]:
        bars = []
        with path.open(newline="") as f:
            for row in csv.DictReader(f):
                bars.append(
                    Bar(
                        instrument=instrument,
                        timestamp=datetime.fromisoformat(row["timestamp"]),
                        open=Decimal(row["open"]),
                        high=Decimal(row["high"]),
                        low=Decimal(row["low"]),
                        close=Decimal(row["close"]),
                        volume=Decimal(row["volume"]),
                        timeframe=timeframe,
                    )
                )
        return bars

    @staticmethod
    def _write_cache(path: Path, bars: list[Bar]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["timestamp", "open", "high", "low", "close", "volume"])
            for b in bars:
                writer.writerow(
                    [b.timestamp.isoformat(), b.open, b.high, b.low, b.close, b.volume]
                )

    # --- fetch ---

    def _token(self) -> str:
        token = self._api_token or os.environ.get("OANDA_PRACTICE_API_TOKEN")
        if not token:
            raise MissingTokenError(
                "OANDA_PRACTICE_API_TOKEN is not set. Copy .env.example to .env and add "
                "your practice API key (free demo account at oanda.com)."
            )
        return token

    def _fetch(
        self, instrument: str, timeframe: str, start: datetime, end: datetime
    ) -> list[Bar]:
        headers = {"Authorization": f"Bearer {self._token()}"}
        client = self._client or httpx.Client(timeout=30)
        step = timedelta(seconds=_GRANULARITY_SECONDS[timeframe])
        bars: list[Bar] = []
        cursor = start
        try:
            while cursor < end:
                resp = client.get(
                    f"{_PRACTICE_HOST}/v3/instruments/{instrument}/candles",
                    headers=headers,
                    params={
                        "granularity": timeframe,
                        "price": "M",
                        "from": cursor.isoformat().replace("+00:00", "Z"),
                        "count": _MAX_CANDLES_PER_REQUEST,
                    },
                )
                resp.raise_for_status()
                candles = resp.json()["candles"]
                new = [c for c in candles if c["complete"]]
                if not new:
                    break
                for c in new:
                    ts = _parse_oanda_time(c["time"])
                    if ts >= end:
                        break
                    mid = c["mid"]
                    bars.append(
                        Bar(
                            instrument=instrument,
                            timestamp=ts,
                            open=Decimal(mid["o"]),
                            high=Decimal(mid["h"]),
                            low=Decimal(mid["l"]),
                            close=Decimal(mid["c"]),
                            volume=Decimal(c["volume"]),
                            timeframe=timeframe,
                        )
                    )
                last_ts = _parse_oanda_time(new[-1]["time"])
                if last_ts >= end or len(candles) < _MAX_CANDLES_PER_REQUEST:
                    break
                cursor = last_ts + step
        finally:
            if self._client is None:
                client.close()
        return bars
