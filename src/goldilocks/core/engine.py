"""Trading engine: wires strategies, connectors, risk, and the state store together.

Responsibilities:
- Instantiate enabled deployments from config; feed bars from connectors to strategies;
  convert Signals to Orders via the ONE shared RiskManager (sizing + gate, see W1).
- SHADOW mode runs everything but logs orders instead of submitting them.
- LIVE requires config `mode: live` + CLI `--live` + no KILL_SWITCH (invariant 4) —
  enforced here even though the CLI also checks; anything failing the triple gate is
  downgraded to SHADOW with a loud warning. `max_total_live_capital` in settings.yaml
  is a fourth, global cap (0 = live disabled entirely).
- Persist deployments, orders, fills, trades, positions, and equity to the SQLite
  state store; the monitor reads ONLY that store.
- On startup, reconcile the state store against broker positions (crash recovery):
  discrepancies are logged and the broker is adopted as truth. v1 assumes at most one
  strategy trades a given instrument per account.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from goldilocks.alerts import AlertHub, DesktopSink, LogSink
from goldilocks.config import DeploymentConfig, Settings
from goldilocks.connectors.base import BrokerConnector
from goldilocks.core import Fill, Order, OrderSide, Signal, TradingMode
from goldilocks.core.portfolio import Portfolio
from goldilocks.core.risk import RiskLimits, RiskManager
from goldilocks.data.base import DataFeed
from goldilocks.data.oanda import _GRANULARITY_SECONDS, OandaDataFeed
from goldilocks.store import StateStore
from goldilocks.strategies import STRATEGY_REGISTRY
from goldilocks.strategies.base import Strategy

logger = logging.getLogger(__name__)


def default_connector_factory(config: DeploymentConfig, mode: TradingMode) -> BrokerConnector:
    from goldilocks.connectors.oanda import OandaConnector
    from goldilocks.core import AssetClass

    if config.asset_class is AssetClass.FOREX:
        return OandaConnector(mode)
    raise ValueError(f"no connector for {config.asset_class} yet (roadmap phase 4)")


@dataclass
class _Deployment:
    config: DeploymentConfig
    mode: TradingMode
    strategy: Strategy
    portfolio: Portfolio
    risk: RiskManager
    connector: BrokerConnector
    last_prices: dict[str, Decimal] = field(default_factory=dict)
    trades_synced: int = 0

    @property
    def name(self) -> str:
        return self.config.strategy


class Engine:
    def __init__(
        self,
        settings: Settings,
        deployments: list[DeploymentConfig],
        live: bool = False,
        store: StateStore | None = None,
        connector_factory=default_connector_factory,
        warmup_feed: DataFeed | None = None,
        alert_hub: AlertHub | None = None,
    ) -> None:
        self.settings = settings
        self.live_flag = live
        self.store = store or StateStore(settings.db_path)
        self._connector_factory = connector_factory
        self._warmup_feed = warmup_feed
        self.alerts = alert_hub or AlertHub(
            [LogSink()] + ([DesktopSink()] if settings.desktop_alerts else [])
        )
        self._stop = asyncio.Event()
        self._deployments = [
            d for d in (self._build(c) for c in deployments) if d is not None
        ]

    # --- setup -------------------------------------------------------------------

    def _resolve_mode(self, config: DeploymentConfig) -> TradingMode | None:
        if not config.enabled or config.mode is TradingMode.BACKTEST:
            return None
        if config.mode is not TradingMode.LIVE:
            return config.mode
        # Invariant 4: live needs config + --live + no kill switch, plus the global cap.
        if not self.live_flag:
            logger.warning("%s: mode=live but --live not passed — DOWNGRADED TO SHADOW",
                           config.strategy)
            return TradingMode.SHADOW
        if self.settings.kill_switch_file.exists():
            logger.warning("%s: KILL_SWITCH present — DOWNGRADED TO SHADOW",
                           config.strategy)
            return TradingMode.SHADOW
        if config.allocation > self.settings.max_total_live_capital:
            logger.warning(
                "%s: allocation %s exceeds max_total_live_capital %s in settings.yaml "
                "— DOWNGRADED TO SHADOW",
                config.strategy, config.allocation, self.settings.max_total_live_capital,
            )
            return TradingMode.SHADOW
        return TradingMode.LIVE

    def _build(self, config: DeploymentConfig) -> _Deployment | None:
        mode = self._resolve_mode(config)
        if mode is None:
            return None
        if config.strategy not in STRATEGY_REGISTRY:
            raise ValueError(f"{config.path}: unknown strategy {config.strategy!r}")
        return _Deployment(
            config=config,
            mode=mode,
            strategy=STRATEGY_REGISTRY[config.strategy](config.params),
            portfolio=Portfolio(initial_cash=config.allocation),
            risk=RiskManager(
                RiskLimits(
                    allocation=config.allocation,
                    max_position_pct=config.max_position_pct,
                    daily_drawdown_halt_pct=self.settings.daily_drawdown_halt_pct,
                ),
                kill_switch_file=self.settings.kill_switch_file,
            ),
            connector=self._connector_factory(config, mode),
        )

    # --- lifecycle ---------------------------------------------------------------

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        if not self._deployments:
            logger.warning("no enabled deployments — nothing to run")
            return
        now = datetime.now(tz=UTC)
        for dep in self._deployments:
            await dep.connector.connect()
            self._restore_state(dep)
            await self._reconcile(dep)
            self.store.record_deployment(
                dep.name, dep.config.asset_class.value, dep.mode.value,
                dep.config.allocation, str(dep.config.path), now,
            )
            self._warmup(dep)
            logger.info("deployment up: %s mode=%s instruments=%s",
                        dep.name, dep.mode.value, dep.config.instruments)

        tasks = []
        for dep in self._deployments:
            tasks.append(asyncio.create_task(self._guard(self._bar_loop(dep), dep)))
            tasks.append(asyncio.create_task(self._guard(self._fill_loop(dep), dep)))
        await self._stop.wait()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        stopped_at = datetime.now(tz=UTC)
        for dep in self._deployments:
            self.store.mark_stopped(dep.name, stopped_at)
            close = getattr(dep.connector, "close", None)
            if close is not None:
                await close()
        logger.info("engine stopped")

    async def _guard(self, coro, dep: _Deployment) -> None:
        """A crashed loop stops the whole engine — never trade half-blind."""
        try:
            await coro
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("%s: loop crashed — stopping engine", dep.name)
            self.alerts.emit(
                "critical", dep.name,
                f"engine loop crashed ({type(exc).__name__}: {exc}) — ENGINE STOPPED",
            )
            self._stop.set()

    # --- startup: restore + reconcile + warmup --------------------------------------

    def _restore_state(self, dep: _Deployment) -> None:
        """W5: rebuild the portfolio by replaying stored fills, and re-arm today's
        drawdown state — a restart must never erase losses or un-latch a halt."""
        fills = self.store.fills_for(dep.name)
        for fill in fills:
            dep.portfolio.apply_fill(fill)
            dep.last_prices.setdefault(fill.instrument, fill.price)
        dep.trades_synced = len(dep.portfolio.closed_trades)
        if fills:
            logger.info("%s: replayed %d stored fills (realized P&L %s)",
                        dep.name, len(fills), dep.portfolio.realized_pnl)
        today = datetime.now(tz=UTC).date()
        day_start = self.store.first_equity_on(dep.name, today)
        halted = self.store.halted_on(dep.name, today)
        if day_start is not None or halted:
            dep.risk.restore_day_state(today, day_start, halted)
            if halted:
                logger.warning("%s: drawdown halt from earlier today is STILL ACTIVE "
                               "— restart does not reset it", dep.name)

    async def _reconcile(self, dep: _Deployment) -> None:
        """Adopt the broker as truth for open positions: any difference from the
        replayed portfolio is corrected with a synthetic fill for the delta (at the
        position's average entry, so no phantom P&L is realized)."""
        broker = {
            p.instrument: p
            for p in await dep.connector.get_positions()
            if p.instrument in dep.config.instruments
        }
        now = datetime.now(tz=UTC)
        for instrument in {p.instrument for p in dep.portfolio.positions} | broker.keys():
            held_pos = dep.portfolio.position(instrument)
            held = held_pos.quantity if held_pos else Decimal(0)
            target = broker[instrument].quantity if instrument in broker else Decimal(0)
            delta = target - held
            if delta == 0:
                continue
            price = (
                broker[instrument].avg_entry_price
                if instrument in broker
                else held_pos.avg_entry_price  # closing at entry: zero phantom P&L
            )
            logger.warning(
                "%s: position mismatch on %s — engine=%s broker=%s; adopting broker "
                "(delta fill %s @ %s)",
                dep.name, instrument, held, target, delta, price,
            )
            fill = Fill(
                order_id=f"reconcile-{uuid.uuid4().hex[:8]}",
                instrument=instrument,
                side=OrderSide.BUY if delta > 0 else OrderSide.SELL,
                quantity=abs(delta),
                price=price,
                filled_at=now,
            )
            dep.portfolio.apply_fill(fill)
            self.store.record_fill(fill, dep.name)  # part of history: replay-safe
            dep.last_prices[instrument] = price
        dep.trades_synced = len(dep.portfolio.closed_trades)
        self.store.replace_positions(dep.name, dep.portfolio.positions, now)

    def _warmup(self, dep: _Deployment) -> None:
        n = dep.strategy.warmup_bars()
        if n <= 0:
            return
        feed = self._warmup_feed or OandaDataFeed(
            cache_dir=self.settings.cache_dir, use_cache=False
        )
        seconds = _GRANULARITY_SECONDS[dep.config.timeframe]
        end = datetime.now(tz=UTC)
        # 3x span: weekends/holidays produce no bars, so over-fetch and keep the tail.
        start = end - timedelta(seconds=seconds * n * 3)
        for instrument in dep.config.instruments:
            bars = feed.get_bars(instrument, dep.config.timeframe, start, end)
            for bar in bars[-n:]:
                dep.strategy.on_bar(bar)  # signals discarded: warmup is silent
                dep.last_prices[instrument] = bar.close

    # --- run loops -----------------------------------------------------------------

    async def _bar_loop(self, dep: _Deployment) -> None:
        stream = dep.connector.stream_bars(dep.config.instruments, dep.config.timeframe)
        async for bar in stream:
            await self._on_bar(dep, bar)

    async def _fill_loop(self, dep: _Deployment) -> None:
        async for fill in dep.connector.stream_fills():
            self._on_fill(dep, fill)

    async def _on_bar(self, dep: _Deployment, bar) -> None:
        dep.last_prices[bar.instrument] = bar.close
        for signal in dep.strategy.on_bar(bar):
            await self._handle_signal(dep, signal)
        equity = dep.portfolio.equity(dep.last_prices)
        was_halted = dep.risk.halted
        dep.risk.record_equity(bar.timestamp, equity)
        if dep.risk.halted and not was_halted:
            reason = (f"daily drawdown halt: equity {equity} breached "
                      f"{dep.risk.limits.daily_drawdown_halt_pct}% day loss limit")
            logger.critical("%s: %s", dep.name, reason)
            self.store.record_halt(dep.name, bar.timestamp, reason)
            self.alerts.emit("critical", dep.name, reason, bar.timestamp)
        self.store.record_equity(dep.name, bar.timestamp, equity)

    async def _handle_signal(self, dep: _Deployment, signal: Signal) -> None:
        price = dep.last_prices.get(signal.instrument)
        if price is None:
            logger.warning("%s: no price yet for %s — signal dropped",
                           dep.name, signal.instrument)
            return
        quantity = dep.risk.size_signal(signal, dep.portfolio, price)
        if quantity <= 0:
            return
        order = Order(
            order_id=uuid.uuid4().hex,
            strategy_name=dep.name,
            instrument=signal.instrument,
            side=signal.side,
            quantity=quantity,
            mode=dep.mode,
            created_at=datetime.now(tz=UTC),
        )
        verdict = dep.risk.check_order(order, dep.portfolio, price)
        if not verdict.approved:
            logger.warning("%s: order rejected — %s", dep.name, verdict.reason)
            self.store.record_order(order, "rejected", verdict.reason)
            self.alerts.emit("warning", dep.name, f"order rejected: {verdict.reason}")
            return
        if dep.mode is TradingMode.SHADOW:
            logger.info("%s [SHADOW]: would submit %s %s %s (%s)",
                        dep.name, order.side.value, order.quantity,
                        order.instrument, signal.reason)
            self.store.record_order(order, "shadow", signal.reason)
            return
        self.store.record_order(order, "submitted", signal.reason)
        try:
            broker_id = await dep.connector.submit_order(order)
        except Exception as exc:
            logger.error("%s: submit failed — %s", dep.name, exc)
            self.store.update_order_status(order.order_id, "error", str(exc))
            self.alerts.emit("warning", dep.name, f"order submit failed: {exc}")
            return
        self.store.update_order_status(order.order_id, "accepted", broker_id)

    def _on_fill(self, dep: _Deployment, fill: Fill) -> None:
        dep.portfolio.apply_fill(fill)
        dep.strategy.on_fill(fill)
        self.store.record_fill(fill, dep.name)
        self.store.update_order_status(fill.order_id, "filled")
        for trade in dep.portfolio.closed_trades[dep.trades_synced:]:
            self.store.record_trade(trade, dep.name)
        dep.trades_synced = len(dep.portfolio.closed_trades)
        self.store.replace_positions(dep.name, dep.portfolio.positions, fill.filled_at)
        logger.info("%s: filled %s %s %s @ %s", dep.name, fill.side.value,
                    fill.quantity, fill.instrument, fill.price)
