"""Example forex strategy: EMA crossover on EUR/USD.

Exists to prove the pipeline end to end (roadmap phase 1) — not expected to be
profitable. Long when fast EMA crosses above slow EMA, flat when it crosses below.
"""

from __future__ import annotations

from decimal import Decimal

from goldilocks.core import AssetClass, Bar, OrderSide, Signal
from goldilocks.strategies.base import Strategy, register


@register
class EmaCross(Strategy):
    name = "ema_cross"
    asset_class = AssetClass.FOREX

    def __init__(self, params: dict | None = None) -> None:
        super().__init__(params)
        self.fast_period = int(self.params.get("fast_period", 12))
        self.slow_period = int(self.params.get("slow_period", 26))
        self._fast_ema: Decimal | None = None
        self._slow_ema: Decimal | None = None
        self._was_above: bool | None = None

    def warmup_bars(self) -> int:
        return self.slow_period

    @staticmethod
    def _ema(prev: Decimal | None, price: Decimal, period: int) -> Decimal:
        if prev is None:
            return price
        k = Decimal(2) / (period + 1)
        return price * k + prev * (1 - k)

    def on_bar(self, bar: Bar) -> list[Signal]:
        self._fast_ema = self._ema(self._fast_ema, bar.close, self.fast_period)
        self._slow_ema = self._ema(self._slow_ema, bar.close, self.slow_period)

        is_above = self._fast_ema > self._slow_ema
        crossed = self._was_above is not None and is_above != self._was_above
        self._was_above = is_above

        if not crossed:
            return []
        if is_above:
            return [Signal(bar.instrument, OrderSide.BUY, reason="fast EMA crossed above slow")]
        return [
            Signal(
                bar.instrument,
                OrderSide.SELL,
                exit=True,
                reason="fast EMA crossed below slow",
            )
        ]
