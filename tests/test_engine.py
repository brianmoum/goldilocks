"""Engine run loop: signal -> shared risk -> order -> fill -> portfolio + state store.
Uses a fake in-memory connector; every safety behavior (shadow, live downgrades,
rejection, reconcile) is asserted against the store, because that's what the monitor
will show the human."""

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from goldilocks.config import DeploymentConfig, Settings
from goldilocks.connectors.base import BrokerConnector
from goldilocks.core import (
    AssetClass,
    Bar,
    Fill,
    OrderSide,
    Position,
    Signal,
    TradingMode,
)
from goldilocks.core.engine import Engine
from goldilocks.store import StateStore
from goldilocks.strategies.base import Strategy, register

T0 = datetime(2026, 7, 6, 10, 0, tzinfo=UTC)


@register
class EngineTestStrategy(Strategy):
    """Emits whatever the test queued on `next_signals`."""

    name = "engine_test"
    asset_class = AssetClass.FOREX

    def __init__(self, params=None):
        super().__init__(params)
        self.next_signals: list[Signal] = []
        self.fills: list[Fill] = []

    def on_bar(self, bar: Bar) -> list[Signal]:
        signals, self.next_signals = self.next_signals, []
        return signals

    def on_fill(self, fill: Fill) -> None:
        self.fills.append(fill)


class FakeConnector(BrokerConnector):
    supports = {AssetClass.FOREX}

    def __init__(self, mode: TradingMode):
        super().__init__(mode)
        self.bar_queue: asyncio.Queue = asyncio.Queue()
        self.submitted = []
        self.broker_positions: list[Position] = []
        self.fill_price = Decimal("1.1000")
        self.bar_stream_error: Exception | None = None
        self._fill_queue: asyncio.Queue = asyncio.Queue()

    async def connect(self):
        pass

    async def get_account(self):
        raise NotImplementedError

    async def get_positions(self):
        return list(self.broker_positions)

    async def submit_order(self, order):
        self.submitted.append(order)
        self._fill_queue.put_nowait(
            Fill(order.order_id, order.instrument, order.side, order.quantity,
                 self.fill_price, T0)
        )
        return f"broker-{len(self.submitted)}"

    async def cancel_order(self, broker_order_id):
        pass

    async def stream_bars(self, instruments, timeframe):
        while True:
            bar = await self.bar_queue.get()
            if self.bar_stream_error is not None:
                raise self.bar_stream_error
            yield bar

    async def stream_fills(self):
        while True:
            yield await self._fill_queue.get()


def make_bar(close: str, minute: int = 0) -> Bar:
    price = Decimal(close)
    return Bar("EUR_USD", T0.replace(minute=minute), price, price, price, price,
               Decimal(100), "M15")


def make_config(tmp_path, mode="paper", allocation="1000") -> DeploymentConfig:
    return DeploymentConfig(
        path=tmp_path / "engine_test.yaml",
        strategy="engine_test",
        asset_class=AssetClass.FOREX,
        mode=TradingMode(mode),
        enabled=True,
        allocation=Decimal(allocation),
        max_position_pct=Decimal(50),
        instruments=["EUR_USD"],
    )


def make_engine(tmp_path, mode="paper", live=False, settings=None):
    settings = settings or Settings(
        db_path=tmp_path / "state" / "test.db",
        kill_switch_file=tmp_path / "KILL_SWITCH",
    )
    created: list[FakeConnector] = []

    def factory(cfg, resolved_mode):
        connector = FakeConnector(resolved_mode)
        created.append(connector)
        return connector

    engine = Engine(
        settings,
        [make_config(tmp_path, mode=mode)],
        live=live,
        connector_factory=factory,
    )
    return engine, created[0]


async def drive(engine, connector, steps):
    task = asyncio.create_task(engine.run())
    await asyncio.sleep(0.02)
    for step in steps:
        connector.bar_queue.put_nowait(step)
        await asyncio.sleep(0.02)
    engine.stop()
    await asyncio.wait_for(task, timeout=2)


def test_paper_flow_signal_to_fill_to_store(tmp_path):
    engine, connector = make_engine(tmp_path)

    async def scenario():
        dep = engine._deployments[0]
        dep.strategy.next_signals = [Signal("EUR_USD", OrderSide.BUY, reason="test buy")]
        await drive(engine, connector, [make_bar("1.1000"), make_bar("1.1000", 15)])

    asyncio.run(scenario())
    dep = engine._deployments[0]
    # 50% of 1000 at 1.10 = 454 units, sized by the shared RiskManager
    assert len(connector.submitted) == 1
    assert connector.submitted[0].quantity == Decimal(454)
    pos = dep.portfolio.position("EUR_USD")
    assert pos is not None and pos.quantity == Decimal(454)
    assert dep.strategy.fills, "strategy must be notified of its fill"

    store = StateStore(tmp_path / "state" / "test.db")
    (row,) = store.status_rows()
    assert row.mode == "paper"
    assert row.exposure == Decimal(454) * Decimal("1.1000")
    order_row = store._conn.execute("SELECT status FROM orders").fetchone()
    assert order_row["status"] == "filled"
    assert row.stopped


def test_shadow_mode_never_submits(tmp_path):
    engine, connector = make_engine(tmp_path, mode="shadow")

    async def scenario():
        dep = engine._deployments[0]
        dep.strategy.next_signals = [Signal("EUR_USD", OrderSide.BUY)]
        await drive(engine, connector, [make_bar("1.1000")])

    asyncio.run(scenario())
    assert connector.submitted == []
    store = StateStore(tmp_path / "state" / "test.db")
    order_row = store._conn.execute("SELECT status FROM orders").fetchone()
    assert order_row["status"] == "shadow"


def test_live_without_flag_downgrades_to_shadow(tmp_path):
    engine, _ = make_engine(tmp_path, mode="live", live=False)
    assert engine._deployments[0].mode is TradingMode.SHADOW


def test_live_with_flag_but_zero_global_cap_downgrades(tmp_path):
    # settings default: max_total_live_capital = 0 -> live globally disabled
    engine, _ = make_engine(tmp_path, mode="live", live=True)
    assert engine._deployments[0].mode is TradingMode.SHADOW


def test_live_with_kill_switch_downgrades(tmp_path):
    (tmp_path / "KILL_SWITCH").touch()
    settings = Settings(
        db_path=tmp_path / "state" / "test.db",
        kill_switch_file=tmp_path / "KILL_SWITCH",
        max_total_live_capital=Decimal(10000),
    )
    engine, _ = make_engine(tmp_path, mode="live", live=True, settings=settings)
    assert engine._deployments[0].mode is TradingMode.SHADOW


def test_live_all_gates_open_stays_live(tmp_path):
    settings = Settings(
        db_path=tmp_path / "state" / "test.db",
        kill_switch_file=tmp_path / "KILL_SWITCH",
        max_total_live_capital=Decimal(10000),
    )
    engine, _ = make_engine(tmp_path, mode="live", live=True, settings=settings)
    assert engine._deployments[0].mode is TradingMode.LIVE


def test_disabled_deployment_skipped(tmp_path):
    config = DeploymentConfig(
        path=tmp_path / "x.yaml", strategy="engine_test",
        asset_class=AssetClass.FOREX, mode=TradingMode.PAPER, enabled=False,
        allocation=Decimal(1000), max_position_pct=Decimal(50),
        instruments=["EUR_USD"],
    )
    engine = Engine(
        Settings(db_path=tmp_path / "s.db", kill_switch_file=tmp_path / "K"),
        [config],
        connector_factory=lambda cfg, m: FakeConnector(m),
    )
    assert engine._deployments == []


def _named_config(tmp_path, stem: str, instrument: str) -> DeploymentConfig:
    return DeploymentConfig(
        path=tmp_path / f"{stem}.yaml", strategy="engine_test",
        asset_class=AssetClass.FOREX, mode=TradingMode.PAPER, enabled=True,
        allocation=Decimal(1000), max_position_pct=Decimal(50),
        instruments=[instrument],
    )


def test_two_deployments_of_one_strategy_stay_separate(tmp_path):
    """Deployments are keyed by YAML stem, not strategy name: running the same
    strategy on two instruments must not merge their orders/fills/equity."""
    engine = Engine(
        Settings(db_path=tmp_path / "state" / "test.db", kill_switch_file=tmp_path / "K"),
        [_named_config(tmp_path, "ema_eurusd", "EUR_USD"),
         _named_config(tmp_path, "ema_gbpusd", "GBP_USD")],
        connector_factory=lambda cfg, m: FakeConnector(m),
    )
    assert [d.name for d in engine._deployments] == ["ema_eurusd", "ema_gbpusd"]


def test_duplicate_deployment_names_rejected(tmp_path):
    with pytest.raises(ValueError, match="duplicate deployment names"):
        Engine(
            Settings(db_path=tmp_path / "state" / "test.db", kill_switch_file=tmp_path / "K"),
            [_named_config(tmp_path, "same", "EUR_USD"),
             _named_config(tmp_path, "same", "GBP_USD")],
            connector_factory=lambda cfg, m: FakeConnector(m),
        )


def test_reconcile_adopts_broker_position(tmp_path):
    engine, connector = make_engine(tmp_path)
    connector.broker_positions = [
        Position("EUR_USD", Decimal(200), Decimal("1.0900"))
    ]

    async def scenario():
        await drive(engine, connector, [])

    asyncio.run(scenario())
    dep = engine._deployments[0]
    pos = dep.portfolio.position("EUR_USD")
    assert pos is not None
    assert pos.quantity == Decimal(200)
    assert pos.avg_entry_price == Decimal("1.0900")
    # equity still marks to exactly the allocation at the seed price
    assert dep.portfolio.equity({"EUR_USD": Decimal("1.0900")}) == Decimal(1000)
    store = StateStore(tmp_path / "state" / "test.db")
    assert store.positions_for("engine_test")[0].quantity == Decimal(200)


def test_risk_rejection_recorded_not_submitted(tmp_path):
    engine, connector = make_engine(tmp_path)

    async def scenario():
        dep = engine._deployments[0]
        # Force a signal whose sized order would breach the position cap by
        # pre-seeding an opposite position bigger than allowed... simpler: halt.
        dep.risk.record_equity(T0, Decimal(1000))
        dep.risk.record_equity(T0.replace(minute=5), Decimal(900))
        assert dep.risk.halted
        dep.strategy.next_signals = [Signal("EUR_USD", OrderSide.BUY)]
        await drive(engine, connector, [make_bar("1.1000")])

    asyncio.run(scenario())
    assert connector.submitted == []
    store = StateStore(tmp_path / "state" / "test.db")
    order_row = store._conn.execute("SELECT status, reason FROM orders").fetchone()
    assert order_row["status"] == "rejected"
    assert "drawdown" in order_row["reason"]
