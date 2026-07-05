"""OANDA v20 connector — forex execution (roadmap phase 2).

Practice and live are different hosts AND different tokens:
  practice: https://api-fxpractice.oanda.com   (OANDA_PRACTICE_API_TOKEN / _ACCOUNT_ID)
  live:     https://api-fxtrade.oanda.com      (OANDA_LIVE_API_TOKEN / _ACCOUNT_ID)
The credential set is chosen strictly by TradingMode at construction and can never be
mixed: PAPER/SHADOW read only the practice variables, LIVE only the live ones.

v1 scope: market orders (FOK). Fills are parsed from the order response and delivered
through stream_fills(); bars come from polling the candles endpoint for newly completed
candles (the tick-stream endpoint can replace this later without touching the engine).
Quantities are base-currency units, signed for direction, whole numbers.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal

import httpx

from goldilocks.connectors.base import BrokerConnector
from goldilocks.core import AssetClass, Bar, Fill, Order, OrderSide, Position, TradingMode
from goldilocks.core.types import AccountSnapshot
from goldilocks.data.oanda import _GRANULARITY_SECONDS, _parse_oanda_time

logger = logging.getLogger(__name__)

_HOSTS = {
    TradingMode.PAPER: "https://api-fxpractice.oanda.com",
    TradingMode.SHADOW: "https://api-fxpractice.oanda.com",
    TradingMode.LIVE: "https://api-fxtrade.oanda.com",
}
_ENV_PREFIX = {
    TradingMode.PAPER: "OANDA_PRACTICE",
    TradingMode.SHADOW: "OANDA_PRACTICE",
    TradingMode.LIVE: "OANDA_LIVE",
}


class OandaError(RuntimeError):
    pass


class OandaConnector(BrokerConnector):
    supports = {AssetClass.FOREX}

    def __init__(
        self,
        mode: TradingMode,
        api_token: str | None = None,
        account_id: str | None = None,
        client: httpx.AsyncClient | None = None,
        poll_interval: float = 5.0,
        retry_backoff: float = 1.0,
    ) -> None:
        super().__init__(mode)
        self.host = _HOSTS[mode]
        prefix = _ENV_PREFIX[mode]
        self._token = api_token or os.environ.get(f"{prefix}_API_TOKEN")
        self._account_id = account_id or os.environ.get(f"{prefix}_ACCOUNT_ID")
        if not self._token or not self._account_id:
            raise OandaError(
                f"{prefix}_API_TOKEN / {prefix}_ACCOUNT_ID not set for mode "
                f"{mode.value!r} (see .env.example; the gl shell function injects them)"
            )
        self._client = client or httpx.AsyncClient(
            timeout=30, headers={"Authorization": f"Bearer {self._token}"}
        )
        self._poll_interval = poll_interval
        self._retry_backoff = retry_backoff
        self._fill_queue: asyncio.Queue[Fill] = asyncio.Queue()

    async def close(self) -> None:
        await self._client.aclose()

    def _url(self, path: str) -> str:
        return f"{self.host}/v3/accounts/{self._account_id}{path}"

    # --- account ---

    async def connect(self) -> None:
        """Validate credentials and account reachability; fail fast on mismatch."""
        resp = await self._client.get(self._url("/summary"))
        if resp.status_code in (401, 403):
            raise OandaError(
                f"OANDA rejected the {self.mode.value} credentials (HTTP "
                f"{resp.status_code}) — token/account pair may be mixed or expired"
            )
        resp.raise_for_status()

    async def get_account(self) -> AccountSnapshot:
        resp = await self._client.get(self._url("/summary"))
        resp.raise_for_status()
        acct = resp.json()["account"]
        return AccountSnapshot(
            balance=Decimal(acct["balance"]),
            currency=acct["currency"],
            positions=await self.get_positions(),
            taken_at=datetime.now(tz=UTC),
        )

    async def get_positions(self) -> list[Position]:
        resp = await self._client.get(self._url("/openPositions"))
        resp.raise_for_status()
        out = []
        for p in resp.json()["positions"]:
            long_units = Decimal(p["long"]["units"])
            short_units = Decimal(p["short"]["units"])
            net = long_units + short_units  # short units are negative
            if net == 0:
                continue
            side = p["long"] if net > 0 else p["short"]
            out.append(
                Position(
                    instrument=p["instrument"],
                    quantity=net,
                    avg_entry_price=Decimal(side["averagePrice"]),
                )
            )
        return out

    # --- orders ---

    async def submit_order(self, order: Order) -> str:
        if order.mode is not self.mode:
            raise OandaError(
                f"order mode {order.mode.value!r} does not match connector mode "
                f"{self.mode.value!r} — refusing to submit"
            )
        units = order.quantity if order.side is OrderSide.BUY else -order.quantity
        body = {
            "order": {
                "type": "MARKET",
                "instrument": order.instrument,
                "units": str(int(units)),
                "timeInForce": "FOK",
                "positionFill": "DEFAULT",
            }
        }
        resp = await self._client.post(self._url("/orders"), json=body)
        resp.raise_for_status()
        data = resp.json()
        if "orderRejectTransaction" in data:
            reason = data["orderRejectTransaction"].get("rejectReason", "unknown")
            raise OandaError(f"order rejected by OANDA: {reason}")
        if "orderCancelTransaction" in data:
            reason = data["orderCancelTransaction"].get("reason", "unknown")
            raise OandaError(f"order cancelled by OANDA: {reason}")
        fill_txn = data.get("orderFillTransaction")
        if fill_txn is not None:
            fill = Fill(
                order_id=order.order_id,
                instrument=order.instrument,
                side=order.side,
                quantity=abs(Decimal(fill_txn["units"])),
                price=Decimal(fill_txn["price"]),
                filled_at=_parse_oanda_time(fill_txn["time"]),
                commission=Decimal(fill_txn.get("commission", "0")),
            )
            self._fill_queue.put_nowait(fill)
        return data["orderCreateTransaction"]["id"]

    async def cancel_order(self, broker_order_id: str) -> None:
        resp = await self._client.put(self._url(f"/orders/{broker_order_id}/cancel"))
        resp.raise_for_status()

    # --- streams ---

    async def stream_fills(self) -> AsyncIterator[Fill]:
        while True:
            yield await self._fill_queue.get()

    async def stream_bars(
        self, instruments: list[str], timeframe: str
    ) -> AsyncIterator[Bar]:
        """Poll for newly completed candles and yield them once, oldest first.

        Polling (default every 5s) is deliberate for v1: completed candles are the
        engine's unit of work, and the candles endpoint is authoritative for them.

        Resilience (W4): transient failures — transport errors, 429, 5xx — are retried
        here with exponential backoff and never reach the engine; auth/config errors
        (other 4xx) stay fatal. The poll size scales with time since the last seen bar,
        so candles that completed during an outage are backfilled, oldest first.
        """
        if timeframe not in _GRANULARITY_SECONDS:
            raise ValueError(f"unknown OANDA granularity: {timeframe!r}")
        seconds = _GRANULARITY_SECONDS[timeframe]
        last_seen: dict[str, datetime] = {}
        backoff = self._retry_backoff
        while True:
            try:
                for instrument in instruments:
                    count = 3
                    if instrument in last_seen:
                        elapsed = datetime.now(tz=UTC) - last_seen[instrument]
                        count = min(500, max(3, int(elapsed.total_seconds() / seconds) + 2))
                    resp = await self._client.get(
                        f"{self.host}/v3/instruments/{instrument}/candles",
                        params={"granularity": timeframe, "price": "M", "count": count},
                    )
                    resp.raise_for_status()
                    complete = [c for c in resp.json()["candles"] if c["complete"]]
                    if instrument not in last_seen:
                        # First poll primes the cursor without yielding: history up to
                        # now is the engine's warmup feed, and a bar must never arrive
                        # twice.
                        if complete:
                            last_seen[instrument] = _parse_oanda_time(complete[-1]["time"])
                        continue
                    for c in complete:
                        ts = _parse_oanda_time(c["time"])
                        if ts <= last_seen[instrument]:
                            continue
                        last_seen[instrument] = ts
                        mid = c["mid"]
                        yield Bar(
                            instrument=instrument,
                            timestamp=ts,
                            open=Decimal(mid["o"]),
                            high=Decimal(mid["h"]),
                            low=Decimal(mid["l"]),
                            close=Decimal(mid["c"]),
                            volume=Decimal(c["volume"]),
                            timeframe=timeframe,
                        )
                backoff = self._retry_backoff  # healthy poll resets the backoff
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status != 429 and status < 500:
                    raise  # 401/403/404: credentials or config — fatal, stop the engine
                logger.warning("bar poll got HTTP %s — retrying in %.0fs", status, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
                continue
            except httpx.TransportError as exc:
                logger.warning("bar poll transport error (%s: %s) — retrying in %.0fs",
                               type(exc).__name__, exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
                continue
            await asyncio.sleep(self._poll_interval)
