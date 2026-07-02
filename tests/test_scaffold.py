"""Smoke tests: the scaffold imports, the registry works, and the example strategy
emits sane signals. Grows real coverage as phases land."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from goldilocks.core import AssetClass, Bar, OrderSide
from goldilocks.strategies import STRATEGY_REGISTRY


def make_bar(close: str, i: int) -> Bar:
    price = Decimal(close)
    return Bar(
        instrument="EUR_USD",
        timestamp=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=15 * i),
        open=price,
        high=price,
        low=price,
        close=price,
        volume=Decimal(100),
        timeframe="M15",
    )


def test_registry_contains_example_strategy():
    assert "ema_cross" in STRATEGY_REGISTRY
    assert STRATEGY_REGISTRY["ema_cross"].asset_class is AssetClass.FOREX


def test_ema_cross_signals_on_crossover():
    strat = STRATEGY_REGISTRY["ema_cross"]({"fast_period": 2, "slow_period": 4})
    signals = []
    # Downtrend then sharp uptrend: fast EMA must cross above slow exactly once.
    prices = ["1.10", "1.09", "1.08", "1.07", "1.06", "1.12", "1.14", "1.16"]
    for i, p in enumerate(prices):
        signals += strat.on_bar(make_bar(p, i))
    buys = [s for s in signals if s.side is OrderSide.BUY]
    assert len(buys) == 1
    assert not buys[0].exit
