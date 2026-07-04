"""Backtest engine: fill timing (no lookahead), spread application, sizing, metrics."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from goldilocks.backtest import BacktestConfig, BacktestEngine
from goldilocks.core import AssetClass, Bar, Fill, OrderSide, Signal
from goldilocks.strategies.base import Strategy


def make_bars(opens: list[str], step_pips: str = "0.0000") -> list[Bar]:
    """Each bar opens at the given price; close = open + step."""
    bars = []
    for i, o in enumerate(opens):
        op = Decimal(o)
        cl = op + Decimal(step_pips)
        bars.append(
            Bar(
                instrument="EUR_USD",
                timestamp=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=15 * i),
                open=op,
                high=max(op, cl),
                low=min(op, cl),
                close=cl,
                volume=Decimal(100),
                timeframe="M15",
            )
        )
    return bars


class BuyBarTwo(Strategy):
    """Deterministic test strategy: buy on bar index 2, exit on bar index 4."""

    name = "buy_bar_two"
    asset_class = AssetClass.FOREX

    def __init__(self, params=None):
        super().__init__(params)
        self.seen = 0
        self.fills: list[Fill] = []

    def on_bar(self, bar: Bar) -> list[Signal]:
        self.seen += 1
        if self.seen == 3:  # bar index 2
            return [Signal(bar.instrument, OrderSide.BUY)]
        if self.seen == 5:  # bar index 4
            return [Signal(bar.instrument, OrderSide.SELL, exit=True)]
        return []

    def on_fill(self, fill: Fill) -> None:
        self.fills.append(fill)


def run_buy_bar_two(spread="0.0002"):
    strategy = BuyBarTwo()
    engine = BacktestEngine(
        strategy,
        BacktestConfig(
            allocation=Decimal(1000),
            max_position_pct=Decimal(50),
            spread=Decimal(spread),
        ),
    )
    bars = make_bars(["1.1000", "1.1000", "1.1000", "1.2000", "1.2000", "1.3000"])
    return engine.run(bars), strategy


def test_fills_at_next_bar_open_with_spread():
    result, strategy = run_buy_bar_two()
    assert len(strategy.fills) == 2
    entry, exit_ = strategy.fills
    # Signal on bar 2 (open 1.1000) must fill at bar 3's open, not bar 2's prices.
    assert entry.price == Decimal("1.2000") + Decimal("0.0001")  # half spread
    assert exit_.price == Decimal("1.3000") - Decimal("0.0001")
    assert entry.filled_at == datetime(2026, 1, 1, 0, 45, tzinfo=UTC)


def test_sizing_respects_max_position_pct():
    _, strategy = run_buy_bar_two(spread="0")
    entry = strategy.fills[0]
    # 50% of 1000 = 500 notional at 1.20 open -> 416 whole units
    assert entry.quantity == Decimal(416)


def test_result_metrics():
    result, _ = run_buy_bar_two(spread="0")
    assert result.bars == 6
    assert len(result.trades) == 1
    t = result.trades[0]
    assert t.pnl == Decimal("41.6000")  # 416 * (1.30 - 1.20)
    assert result.win_rate_pct == Decimal(100)
    assert result.profit_factor == Decimal("Infinity")
    assert result.final_equity == Decimal(1000) + t.pnl
    assert result.total_return_pct == t.pnl / 10
    assert result.open_position is None
    assert "Trade list" in result.report()


def test_warmup_signals_are_discarded():
    class SignalEveryBar(Strategy):
        name = "signal_every_bar"
        asset_class = AssetClass.FOREX

        def on_bar(self, bar: Bar) -> list[Signal]:
            return [Signal(bar.instrument, OrderSide.BUY)]

        def warmup_bars(self) -> int:
            return 3

    strategy = SignalEveryBar()
    engine = BacktestEngine(
        strategy,
        BacktestConfig(allocation=Decimal(1000), max_position_pct=Decimal(50)),
    )
    engine.run(make_bars(["1.1"] * 5))
    # Signals from bars 0-2 discarded; bar 3's signal fills at bar 4's open,
    # bar 4's signal never fills (no bar 5). Exactly one position opened.
    pos = engine.portfolio.position("EUR_USD")
    assert pos is not None
    assert engine.portfolio.cash == Decimal(1000) - pos.quantity * Decimal("1.1")


def test_ema_cross_end_to_end():
    """The example strategy runs through the engine on synthetic data."""
    from goldilocks.strategies import STRATEGY_REGISTRY

    strategy = STRATEGY_REGISTRY["ema_cross"]({"fast_period": 2, "slow_period": 4})
    engine = BacktestEngine(
        strategy,
        BacktestConfig(
            allocation=Decimal(1000),
            max_position_pct=Decimal(50),
            spread=Decimal("0.0002"),
        ),
    )
    # Down, sharply up (golden cross -> buy), then down (death cross -> exit).
    prices = ["1.10", "1.09", "1.08", "1.07", "1.06", "1.12", "1.14", "1.16",
              "1.10", "1.05", "1.04", "1.03"]
    result = engine.run(make_bars(prices))
    assert len(result.trades) == 1
    assert result.open_position is None
    assert result.trades[0].side is OrderSide.BUY
