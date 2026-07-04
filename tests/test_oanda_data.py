"""OANDA data adapter: request shape, pagination, parsing, and CSV cache round-trip.
Uses httpx.MockTransport — no network, no credentials."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest

from goldilocks.data.oanda import MissingTokenError, OandaDataFeed, _parse_oanda_time

START = datetime(2026, 1, 1, tzinfo=UTC)


def candle(ts: datetime, price: str, complete: bool = True) -> dict:
    return {
        "time": ts.strftime("%Y-%m-%dT%H:%M:%S.000000000Z"),
        "complete": complete,
        "volume": 42,
        "mid": {"o": price, "h": price, "l": price, "c": price},
    }


def make_feed(tmp_path, handler) -> tuple[OandaDataFeed, httpx.Client]:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return (
        OandaDataFeed(api_token="test-token", cache_dir=tmp_path, client=client),
        client,
    )


def test_fetch_parses_and_caches(tmp_path):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        candles = [candle(START + timedelta(minutes=15 * i), "1.1000") for i in range(4)]
        candles.append(candle(START + timedelta(minutes=60), "1.1000", complete=False))
        return httpx.Response(200, json={"candles": candles})

    feed, _ = make_feed(tmp_path, handler)
    end = START + timedelta(hours=2)
    bars = feed.get_bars("EUR_USD", "M15", START, end)

    assert len(bars) == 4  # incomplete candle excluded
    assert bars[0].open == Decimal("1.1000")
    assert bars[0].timestamp == START
    assert bars[0].timestamp.tzinfo is not None

    req = calls[0]
    assert "Bearer test-token" in req.headers["Authorization"]
    assert "api-fxpractice.oanda.com" in str(req.url)
    assert "EUR_USD" in str(req.url)

    # Second call must come from cache: no new requests, identical bars.
    again = feed.get_bars("EUR_USD", "M15", START, end)
    assert len(calls) == 1
    assert again == bars


def test_pagination_advances_cursor(tmp_path):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        cursor = _parse_oanda_time(request.url.params["from"])
        # Full page (5000) first time, then a short page ending the loop.
        n = 5000 if len(calls) == 1 else 10
        candles = [candle(cursor + timedelta(minutes=15 * i), "1.2345") for i in range(n)]
        return httpx.Response(200, json={"candles": candles})

    feed, _ = make_feed(tmp_path, handler)
    end = START + timedelta(minutes=15 * 5010)
    bars = feed.get_bars("EUR_USD", "M15", START, end)

    assert len(calls) == 2
    assert len(bars) == 5010
    # No duplicates or gaps across the page boundary.
    timestamps = [b.timestamp for b in bars]
    assert len(set(timestamps)) == len(timestamps)
    assert timestamps == sorted(timestamps)


def test_bars_beyond_end_excluded(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        candles = [candle(START + timedelta(minutes=15 * i), "1.1") for i in range(10)]
        return httpx.Response(200, json={"candles": candles})

    feed, _ = make_feed(tmp_path, handler)
    bars = feed.get_bars("EUR_USD", "M15", START, START + timedelta(minutes=45))
    assert len(bars) == 3  # 00:00, 00:15, 00:30 — 00:45 is >= end


def test_missing_token_is_a_clear_error(tmp_path, monkeypatch):
    monkeypatch.delenv("OANDA_PRACTICE_API_TOKEN", raising=False)
    feed = OandaDataFeed(cache_dir=tmp_path)
    with pytest.raises(MissingTokenError, match="OANDA_PRACTICE_API_TOKEN"):
        feed.get_bars("EUR_USD", "M15", START, START + timedelta(hours=1))


def test_http_error_propagates(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"errorMessage": "Insufficient authorization"})

    feed, _ = make_feed(tmp_path, handler)
    with pytest.raises(httpx.HTTPStatusError):
        feed.get_bars("EUR_USD", "M15", START, START + timedelta(hours=1))


def test_unknown_granularity_rejected(tmp_path):
    feed = OandaDataFeed(api_token="t", cache_dir=tmp_path)
    with pytest.raises(ValueError, match="granularity"):
        feed.get_bars("EUR_USD", "15m", START, START + timedelta(hours=1))


def test_nanosecond_timestamp_parsing():
    ts = _parse_oanda_time("2026-01-02T03:04:05.123456789Z")
    assert ts == datetime(2026, 1, 2, 3, 4, 5, 123456, tzinfo=UTC)
