"""OANDA connector: credential isolation by mode, order submission, fill delivery,
position parsing, bar-stream dedup. Mocked transport — no network, no credentials."""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest

from goldilocks.connectors.oanda import OandaConnector, OandaError
from goldilocks.core import Order, OrderSide, TradingMode

T0 = datetime(2026, 7, 6, 10, 0, tzinfo=UTC)


def make_connector(handler, mode=TradingMode.PAPER, **kwargs) -> OandaConnector:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OandaConnector(
        mode, api_token="t", account_id="101-001-1-001", client=client,
        poll_interval=0, **kwargs,
    )


def make_order(qty="400", side=OrderSide.BUY, mode=TradingMode.PAPER) -> Order:
    return Order(
        order_id="o1", strategy_name="s", instrument="EUR_USD", side=side,
        quantity=Decimal(qty), mode=mode, created_at=T0,
    )


def test_mode_selects_credential_set(monkeypatch):
    monkeypatch.setenv("OANDA_PRACTICE_API_TOKEN", "practice-token")
    monkeypatch.setenv("OANDA_PRACTICE_ACCOUNT_ID", "prac-acct")
    monkeypatch.delenv("OANDA_LIVE_API_TOKEN", raising=False)
    monkeypatch.delenv("OANDA_LIVE_ACCOUNT_ID", raising=False)

    paper = OandaConnector(TradingMode.PAPER)
    assert paper._token == "practice-token"
    assert "fxpractice" in paper.host

    # LIVE must refuse to fall back to practice credentials.
    with pytest.raises(OandaError, match="OANDA_LIVE"):
        OandaConnector(TradingMode.LIVE)


def test_backtest_mode_rejected():
    with pytest.raises(ValueError, match="backtest"):
        OandaConnector(TradingMode.BACKTEST, api_token="t", account_id="a")


def test_submit_order_sends_signed_units_and_delivers_fill():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        captured.update(json.loads(request.content))
        return httpx.Response(
            201,
            json={
                "orderCreateTransaction": {"id": "42"},
                "orderFillTransaction": {
                    "units": "-400",
                    "price": "1.08350",
                    "time": "2026-07-06T10:00:01.000000000Z",
                    "commission": "0",
                },
            },
        )

    async def run():
        conn = make_connector(handler)
        broker_id = await conn.submit_order(make_order(side=OrderSide.SELL))
        fill = await asyncio.wait_for(anext(conn.stream_fills()), timeout=1)
        return broker_id, fill

    broker_id, fill = asyncio.run(run())
    assert broker_id == "42"
    assert captured["order"]["units"] == "-400"  # sell = negative units
    assert captured["order"]["type"] == "MARKET"
    assert fill.quantity == Decimal(400)
    assert fill.price == Decimal("1.08350")
    assert fill.side is OrderSide.SELL


def test_submit_order_mode_mismatch_refused():
    async def run():
        conn = make_connector(lambda r: httpx.Response(201, json={}))
        await conn.submit_order(make_order(mode=TradingMode.LIVE))

    with pytest.raises(OandaError, match="does not match connector mode"):
        asyncio.run(run())


def test_rejected_order_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            201,
            json={"orderRejectTransaction": {"rejectReason": "INSUFFICIENT_MARGIN"}},
        )

    async def run():
        await make_connector(handler).submit_order(make_order())

    with pytest.raises(OandaError, match="INSUFFICIENT_MARGIN"):
        asyncio.run(run())


def test_get_positions_nets_long_short():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "positions": [
                    {
                        "instrument": "EUR_USD",
                        "long": {"units": "400", "averagePrice": "1.0850"},
                        "short": {"units": "0"},
                    },
                    {
                        "instrument": "GBP_USD",
                        "long": {"units": "0"},
                        "short": {"units": "0"},
                    },
                ]
            },
        )

    positions = asyncio.run(make_connector(handler).get_positions())
    assert len(positions) == 1
    assert positions[0].instrument == "EUR_USD"
    assert positions[0].quantity == Decimal(400)
    assert positions[0].avg_entry_price == Decimal("1.0850")


def test_stream_bars_primes_then_yields_only_new():
    poll_count = 0

    def candle(ts: datetime, complete: bool = True) -> dict:
        return {
            "time": ts.strftime("%Y-%m-%dT%H:%M:%S.000000000Z"),
            "complete": complete,
            "volume": 10,
            "mid": {"o": "1.1", "h": "1.1", "l": "1.1", "c": "1.1"},
        }

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal poll_count
        poll_count += 1
        base = [candle(T0 - timedelta(minutes=30)), candle(T0 - timedelta(minutes=15))]
        if poll_count >= 2:  # a new candle completes after the first poll
            base.append(candle(T0))
        return httpx.Response(200, json={"candles": base + [candle(T0 + timedelta(minutes=15), complete=False)]})

    async def run():
        conn = make_connector(handler)
        stream = conn.stream_bars(["EUR_USD"], "M15")
        bar = await asyncio.wait_for(anext(stream), timeout=1)
        return bar

    bar = asyncio.run(run())
    # The two candles present at priming are never yielded; the first yield is the
    # candle that completed after streaming began.
    assert bar.timestamp == T0
    assert poll_count == 2
