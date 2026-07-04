"""Portfolio accounting: the same math backs backtest and live P&L."""

from datetime import UTC, datetime
from decimal import Decimal

from goldilocks.core import Fill, OrderSide
from goldilocks.core.portfolio import Portfolio


def fill(side: OrderSide, qty: str, price: str, hour: int = 0) -> Fill:
    return Fill(
        order_id=f"o{hour}",
        instrument="EUR_USD",
        side=side,
        quantity=Decimal(qty),
        price=Decimal(price),
        filled_at=datetime(2026, 1, 1, hour, tzinfo=UTC),
    )


def test_long_round_trip_realizes_pnl():
    p = Portfolio(initial_cash=Decimal(1000))
    p.apply_fill(fill(OrderSide.BUY, "400", "1.1000", hour=1))
    p.apply_fill(fill(OrderSide.SELL, "400", "1.1200", hour=2))

    assert p.position("EUR_USD") is None
    assert p.realized_pnl == Decimal("8.0000")  # 400 * 0.02
    assert p.cash == Decimal("1008.0000")
    assert len(p.closed_trades) == 1
    t = p.closed_trades[0]
    assert t.side is OrderSide.BUY
    assert t.entry_price == Decimal("1.1000")
    assert t.exit_price == Decimal("1.1200")


def test_adding_averages_entry_price():
    p = Portfolio(initial_cash=Decimal(1000))
    p.apply_fill(fill(OrderSide.BUY, "100", "1.1000", hour=1))
    p.apply_fill(fill(OrderSide.BUY, "100", "1.1200", hour=2))
    pos = p.position("EUR_USD")
    assert pos is not None
    assert pos.quantity == Decimal(200)
    assert pos.avg_entry_price == Decimal("1.1100")


def test_partial_close_keeps_remainder():
    p = Portfolio(initial_cash=Decimal(1000))
    p.apply_fill(fill(OrderSide.BUY, "300", "1.1000", hour=1))
    p.apply_fill(fill(OrderSide.SELL, "100", "1.1100", hour=2))
    pos = p.position("EUR_USD")
    assert pos is not None
    assert pos.quantity == Decimal(200)
    assert pos.avg_entry_price == Decimal("1.1000")
    assert p.realized_pnl == Decimal("1.0000")
    assert len(p.closed_trades) == 1
    assert p.closed_trades[0].quantity == Decimal(100)


def test_short_round_trip():
    p = Portfolio(initial_cash=Decimal(1000))
    p.apply_fill(fill(OrderSide.SELL, "200", "1.1000", hour=1))
    p.apply_fill(fill(OrderSide.BUY, "200", "1.0900", hour=2))
    assert p.position("EUR_USD") is None
    assert p.realized_pnl == Decimal("2.0000")
    assert p.closed_trades[0].side is OrderSide.SELL


def test_flip_through_zero_opens_new_position():
    p = Portfolio(initial_cash=Decimal(1000))
    p.apply_fill(fill(OrderSide.BUY, "100", "1.1000", hour=1))
    p.apply_fill(fill(OrderSide.SELL, "250", "1.1200", hour=2))
    pos = p.position("EUR_USD")
    assert pos is not None
    assert pos.quantity == Decimal(-150)
    assert pos.avg_entry_price == Decimal("1.1200")
    assert p.realized_pnl == Decimal("2.0000")  # only the closed 100 long


def test_equity_marks_open_positions():
    p = Portfolio(initial_cash=Decimal(1000))
    p.apply_fill(fill(OrderSide.BUY, "400", "1.1000", hour=1))
    # cash = 1000 - 440 = 560; position marked at 1.12 = 448
    assert p.equity({"EUR_USD": Decimal("1.1200")}) == Decimal("1008.0000")
    assert p.unrealized_pnl({"EUR_USD": Decimal("1.1200")}) == Decimal("8.0000")
