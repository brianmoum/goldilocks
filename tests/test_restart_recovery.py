"""W5: risk-critical state survives engine restarts. A restart must never erase the
day's losses, un-latch a drawdown halt, or forget realized P&L."""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from test_engine import FakeConnector, drive, make_bar, make_config, make_engine

from goldilocks.config import Settings
from goldilocks.core import Bar, OrderSide, Position, Signal, TradingMode
from goldilocks.store import StateStore


def bar_today(close: str, minutes_from_now: int = 0) -> Bar:
    """A bar stamped with the REAL current UTC day — restart tests exercise day-based
    risk state, so the bar date must match the restored day."""
    price = Decimal(close)
    ts = datetime.now(tz=UTC) + timedelta(minutes=minutes_from_now)
    return Bar("EUR_USD", ts, price, price, price, price, Decimal(100), "M15")


def restart(tmp_path, connector_positions=None, settings=None):
    """Build a fresh engine over the same state store, as `gl run` would after a stop."""
    engine, connector = make_engine(tmp_path, settings=settings)
    if connector_positions:
        connector.broker_positions = connector_positions
    return engine, connector


def test_halt_survives_restart(tmp_path):
    settings = Settings(
        db_path=tmp_path / "state" / "test.db",
        kill_switch_file=tmp_path / "KILL_SWITCH",
        daily_drawdown_halt_pct=Decimal(5),
    )
    store = StateStore(settings.db_path)
    today = datetime.now(tz=UTC)
    store.record_equity("engine_test", today.replace(hour=0, minute=15), Decimal(1000))
    store.record_halt("engine_test", today.replace(hour=9), "drawdown")

    engine, connector = restart(tmp_path, settings=settings)

    async def scenario():
        dep = engine._deployments[0]
        dep.strategy.next_signals = [Signal("EUR_USD", OrderSide.BUY)]
        await drive(engine, connector, [bar_today("1.1000")])

    asyncio.run(scenario())
    dep = engine._deployments[0]
    assert dep.risk.halted, "restart must not un-latch an active halt"
    assert connector.submitted == [], "halted strategy must not trade after restart"


def test_day_start_equity_survives_restart(tmp_path):
    settings = Settings(
        db_path=tmp_path / "state" / "test.db",
        kill_switch_file=tmp_path / "KILL_SWITCH",
        daily_drawdown_halt_pct=Decimal(5),
    )
    store = StateStore(settings.db_path)
    now = datetime.now(tz=UTC)
    # Day started at 1000; equity is already down 4% before the restart.
    store.record_equity("engine_test", now.replace(hour=0, minute=15), Decimal(1000))
    store.record_equity("engine_test", now.replace(hour=0, minute=30), Decimal(960))

    engine, connector = restart(tmp_path, settings=settings)
    dep = engine._deployments[0]

    async def scenario():
        await drive(engine, connector, [])
        # Post-restart, another 1.5% loss lands: 4% + 1.5% > 5% -> must halt,
        # measured against the STORED day start, not the restart-time equity.
        dep.risk.record_equity(now, Decimal(945))

    asyncio.run(scenario())
    assert dep.risk.halted


def test_fill_replay_restores_pnl_and_positions(tmp_path):
    engine1, connector1 = make_engine(tmp_path)

    async def first_run():
        dep = engine1._deployments[0]
        dep.strategy.next_signals = [Signal("EUR_USD", OrderSide.BUY)]
        await drive(engine1, connector1, [make_bar("1.1000"), make_bar("1.1000", 15)])

    asyncio.run(first_run())
    pos1 = engine1._deployments[0].portfolio.position("EUR_USD")
    assert pos1 is not None and pos1.quantity == Decimal(454)

    # Restart: broker still holds the position the first run opened.
    engine2, _ = restart(
        tmp_path,
        connector_positions=[Position("EUR_USD", Decimal(454), Decimal("1.1000"))],
    )

    async def second_run():
        await drive(engine2, engine2._deployments[0].connector, [])

    asyncio.run(second_run())
    dep2 = engine2._deployments[0]
    pos2 = dep2.portfolio.position("EUR_USD")
    assert pos2 is not None
    assert pos2.quantity == Decimal(454)
    assert pos2.avg_entry_price == Decimal("1.1000")
    # Cash continuity: identical to the pre-restart portfolio, not reset to allocation.
    assert dep2.portfolio.cash == engine1._deployments[0].portfolio.cash
    # No reconcile delta was needed, so no synthetic fill was recorded.
    store = StateStore(tmp_path / "state" / "test.db")
    reconcile_fills = [f for f in store.fills_for("engine_test")
                       if f.order_id.startswith("reconcile")]
    assert reconcile_fills == []


def test_reconcile_corrects_only_the_delta(tmp_path):
    engine1, connector1 = make_engine(tmp_path)

    async def first_run():
        dep = engine1._deployments[0]
        dep.strategy.next_signals = [Signal("EUR_USD", OrderSide.BUY)]
        await drive(engine1, connector1, [make_bar("1.1000"), make_bar("1.1000", 15)])

    asyncio.run(first_run())

    # While the engine was down, 54 units got closed manually at the broker.
    engine2, _ = restart(
        tmp_path,
        connector_positions=[Position("EUR_USD", Decimal(400), Decimal("1.1000"))],
    )

    async def second_run():
        await drive(engine2, engine2._deployments[0].connector, [])

    asyncio.run(second_run())
    dep2 = engine2._deployments[0]
    assert dep2.portfolio.position("EUR_USD").quantity == Decimal(400)
    store = StateStore(tmp_path / "state" / "test.db")
    reconcile_fills = [f for f in store.fills_for("engine_test")
                       if f.order_id.startswith("reconcile")]
    assert len(reconcile_fills) == 1
    assert reconcile_fills[0].quantity == Decimal(54)
    assert reconcile_fills[0].side is OrderSide.SELL


def test_halt_event_recorded_when_triggered_live(tmp_path):
    settings = Settings(
        db_path=tmp_path / "state" / "test.db",
        kill_switch_file=tmp_path / "KILL_SWITCH",
        daily_drawdown_halt_pct=Decimal(5),
    )
    engine, connector = make_engine(tmp_path, settings=settings)

    async def scenario():
        dep = engine._deployments[0]
        dep.strategy.next_signals = [Signal("EUR_USD", OrderSide.BUY)]
        # Fill at 1.10, then the price collapses far past the 5% day limit.
        await drive(engine, connector,
                    [make_bar("1.1000"), make_bar("1.1000", 15), make_bar("0.9000", 30)])

    asyncio.run(scenario())
    dep = engine._deployments[0]
    assert dep.risk.halted
    store = StateStore(tmp_path / "state" / "test.db")
    assert store.halted_on("engine_test", make_bar("1", 0).timestamp.date())


def test_mode_and_fake_connector_reused_from_engine_tests(tmp_path):
    """Guard: the helpers this file borrows keep their contract."""
    engine, connector = make_engine(tmp_path)
    assert isinstance(connector, FakeConnector)
    assert engine._deployments[0].mode is TradingMode.PAPER
    assert make_config(tmp_path).strategy == "engine_test"
