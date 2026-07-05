"""RiskManager: the shared sizing + risk gate (roadmap W1). Both engines route every
signal/order through this component, so its behavior IS the trading behavior."""

from datetime import UTC, datetime
from decimal import Decimal

from goldilocks.core import Fill, Order, OrderSide, Signal, TradingMode
from goldilocks.core.portfolio import Portfolio
from goldilocks.core.risk import RiskLimits, RiskManager

T0 = datetime(2026, 3, 2, 10, 0, tzinfo=UTC)


def make_rm(tmp_path, allocation="1000", max_pos_pct="50", halt_pct="5") -> RiskManager:
    return RiskManager(
        RiskLimits(
            allocation=Decimal(allocation),
            max_position_pct=Decimal(max_pos_pct),
            daily_drawdown_halt_pct=Decimal(halt_pct),
        ),
        kill_switch_file=tmp_path / "KILL_SWITCH",
    )


def make_order(side=OrderSide.BUY, qty="400", mode=TradingMode.PAPER) -> Order:
    return Order(
        order_id="o1",
        strategy_name="test",
        instrument="EUR_USD",
        side=side,
        quantity=Decimal(qty),
        mode=mode,
        created_at=T0,
    )


def portfolio_long(qty="400", price="1.1000") -> Portfolio:
    p = Portfolio(initial_cash=Decimal(1000))
    p.apply_fill(
        Fill("f0", "EUR_USD", OrderSide.BUY, Decimal(qty), Decimal(price), T0)
    )
    return p


# --- sizing ---


def test_entry_sized_from_allocation_and_strength(tmp_path):
    rm = make_rm(tmp_path)
    p = Portfolio(initial_cash=Decimal(1000))
    qty = rm.size_signal(Signal("EUR_USD", OrderSide.BUY), p, Decimal("1.2500"))
    assert qty == Decimal(400)  # 1000 * 50% / 1.25
    half = rm.size_signal(
        Signal("EUR_USD", OrderSide.BUY, strength=Decimal("0.5")), p, Decimal("1.2500")
    )
    assert half == Decimal(200)


def test_exit_sizes_to_full_position(tmp_path):
    rm = make_rm(tmp_path)
    p = portfolio_long("321")
    qty = rm.size_signal(Signal("EUR_USD", OrderSide.SELL, exit=True), p, Decimal("1.1"))
    assert qty == Decimal(321)


def test_exit_with_no_position_is_zero(tmp_path):
    rm = make_rm(tmp_path)
    p = Portfolio(initial_cash=Decimal(1000))
    assert rm.size_signal(
        Signal("EUR_USD", OrderSide.SELL, exit=True), p, Decimal("1.1")
    ) == Decimal(0)


def test_same_direction_entry_not_added(tmp_path):
    rm = make_rm(tmp_path)
    p = portfolio_long()
    assert rm.size_signal(Signal("EUR_USD", OrderSide.BUY), p, Decimal("1.1")) == Decimal(0)


# --- kill switch ---


def test_kill_switch_blocks_paper_even_exits(tmp_path):
    rm = make_rm(tmp_path)
    (tmp_path / "KILL_SWITCH").touch()
    p = portfolio_long()
    exit_order = make_order(OrderSide.SELL, "400")
    verdict = rm.check_order(exit_order, p, Decimal("1.1"))
    assert not verdict.approved
    assert "KILL_SWITCH" in verdict.reason


def test_kill_switch_ignores_backtests(tmp_path):
    rm = make_rm(tmp_path)
    (tmp_path / "KILL_SWITCH").touch()
    p = Portfolio(initial_cash=Decimal(1000))
    verdict = rm.check_order(make_order(mode=TradingMode.BACKTEST), p, Decimal("1.1"))
    assert verdict.approved


# --- drawdown halt ---


def test_drawdown_halt_latches_and_blocks_entries(tmp_path):
    rm = make_rm(tmp_path, halt_pct="5")
    rm.record_equity(T0, Decimal(1000))
    rm.record_equity(T0.replace(hour=11), Decimal(960))
    assert not rm.halted
    rm.record_equity(T0.replace(hour=12), Decimal(950))
    assert rm.halted
    # Recovery within the same day does NOT un-halt.
    rm.record_equity(T0.replace(hour=13), Decimal(990))
    assert rm.halted
    p = Portfolio(initial_cash=Decimal(1000))
    verdict = rm.check_order(make_order(), p, Decimal("1.1"))
    assert not verdict.approved
    assert "drawdown" in verdict.reason


def test_halt_allows_reducing_orders(tmp_path):
    rm = make_rm(tmp_path, halt_pct="5")
    rm.record_equity(T0, Decimal(1000))
    rm.record_equity(T0.replace(hour=12), Decimal(900))
    assert rm.halted
    p = portfolio_long()
    verdict = rm.check_order(make_order(OrderSide.SELL, "400"), p, Decimal("1.1"))
    assert verdict.approved


def test_halt_resets_next_day(tmp_path):
    rm = make_rm(tmp_path, halt_pct="5")
    rm.record_equity(T0, Decimal(1000))
    rm.record_equity(T0.replace(hour=12), Decimal(900))
    assert rm.halted
    next_day = datetime(2026, 3, 3, 0, 15, tzinfo=UTC)
    rm.record_equity(next_day, Decimal(900))
    assert not rm.halted


# --- caps ---


def test_position_cap_rejects_oversized_order(tmp_path):
    rm = make_rm(tmp_path, max_pos_pct="50")
    p = Portfolio(initial_cash=Decimal(1000))
    # 600 units at 1.00 = 600 notional > 500 cap
    verdict = rm.check_order(make_order(qty="600"), p, Decimal("1.0000"))
    assert not verdict.approved
    assert "max position" in verdict.reason


def test_allocation_cap_rejects(tmp_path):
    rm = make_rm(tmp_path, max_pos_pct="200")  # position cap 2000 > allocation 1000
    p = Portfolio(initial_cash=Decimal(1000))
    verdict = rm.check_order(make_order(qty="1500"), p, Decimal("1.0000"))
    assert not verdict.approved
    assert "allocation" in verdict.reason


def test_sized_orders_always_pass_the_gate(tmp_path):
    """What size_signal produces, check_order approves — the two halves agree."""
    rm = make_rm(tmp_path)
    p = Portfolio(initial_cash=Decimal(1000))
    price = Decimal("1.0837")
    qty = rm.size_signal(Signal("EUR_USD", OrderSide.BUY), p, price)
    verdict = rm.check_order(make_order(qty=str(qty)), p, price)
    assert verdict.approved


def test_flip_order_is_risk_increasing(tmp_path):
    """Selling more than the long position opens a short — the excess must obey caps
    and the halt, not sneak through as a 'reduction'."""
    rm = make_rm(tmp_path, halt_pct="5")
    rm.record_equity(T0, Decimal(1000))
    rm.record_equity(T0.replace(hour=12), Decimal(900))
    assert rm.halted
    p = portfolio_long("400")
    verdict = rm.check_order(make_order(OrderSide.SELL, "800"), p, Decimal("1.1"))
    assert not verdict.approved
