"""State store: round-trips preserve Decimal exactness; status aggregation is correct;
a second connection (the monitor) sees the engine's writes."""

from datetime import UTC, datetime
from decimal import Decimal

from goldilocks.core import Fill, Order, OrderSide, Position, TradingMode
from goldilocks.core.portfolio import Trade
from goldilocks.store import StateStore

T0 = datetime(2026, 7, 5, 12, 0, tzinfo=UTC)


def make_store(tmp_path) -> StateStore:
    return StateStore(tmp_path / "state" / "test.db")


def test_order_and_fill_round_trip(tmp_path):
    store = make_store(tmp_path)
    order = Order(
        order_id="o1", strategy_name="ema_cross", instrument="EUR_USD",
        side=OrderSide.BUY, quantity=Decimal("437"), mode=TradingMode.PAPER,
        created_at=T0,
    )
    store.record_order(order, status="submitted")
    store.update_order_status("o1", "filled")
    store.record_fill(
        Fill("o1", "EUR_USD", OrderSide.BUY, Decimal("437"), Decimal("1.08375"), T0),
        strategy_name="ema_cross",
    )
    row = store._conn.execute("SELECT * FROM orders").fetchone()
    assert row["status"] == "filled"
    fill_row = store._conn.execute("SELECT * FROM fills").fetchone()
    assert Decimal(fill_row["price"]) == Decimal("1.08375")


def test_status_rows_aggregate(tmp_path):
    store = make_store(tmp_path)
    store.record_deployment(
        "ema_cross", "forex", "paper", Decimal("1000"), "config/x.yaml", T0
    )
    for pnl in ("5.10", "-2.30", "7.00"):
        store.record_trade(
            Trade(
                instrument="EUR_USD", side=OrderSide.BUY, quantity=Decimal(100),
                entry_price=Decimal("1.1"), exit_price=Decimal("1.2"),
                opened_at=T0, closed_at=T0, pnl=Decimal(pnl),
            ),
            "ema_cross",
        )
    store.replace_positions(
        "ema_cross",
        [Position("EUR_USD", Decimal("400"), Decimal("1.1000"))],
        T0,
    )
    store.record_equity("ema_cross", T0, Decimal("1009.80"))

    (row,) = store.status_rows()
    assert row.realized_pnl == Decimal("9.80")
    assert (row.wins, row.losses) == (2, 1)
    assert row.exposure == Decimal("440.0000")
    assert row.equity == Decimal("1009.80")
    assert not row.stopped


def test_replace_positions_clears_stale(tmp_path):
    store = make_store(tmp_path)
    store.replace_positions(
        "s", [Position("EUR_USD", Decimal(100), Decimal("1.1"))], T0
    )
    store.replace_positions("s", [], T0)
    assert store.positions_for("s") == []


def test_monitor_connection_sees_engine_writes(tmp_path):
    engine_store = make_store(tmp_path)
    engine_store.record_deployment(
        "ema_cross", "forex", "paper", Decimal("1000"), "config/x.yaml", T0
    )
    monitor_store = StateStore(tmp_path / "state" / "test.db")
    assert [r.strategy_name for r in monitor_store.status_rows()] == ["ema_cross"]
    engine_store.mark_stopped("ema_cross", T0)
    assert monitor_store.status_rows()[0].stopped
