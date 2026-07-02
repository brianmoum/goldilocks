"""Strategy interface and registry.

The contract (see CLAUDE.md invariants):
- Strategies consume market data events and emit Signals. They never import connectors,
  never place orders, and never see their own capital limits.
- The exact same subclass runs in backtest, paper, shadow, and live mode.

To add a strategy:

    from goldilocks.core import AssetClass, Bar, Signal
    from goldilocks.strategies.base import Strategy, register

    @register
    class MyStrategy(Strategy):
        name = "my_strategy"
        asset_class = AssetClass.CRYPTO

        def on_bar(self, bar: Bar) -> list[Signal]:
            ...

then deploy it with a YAML in config/strategies/ — no engine changes needed.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar

from goldilocks.core import AssetClass, Bar, Fill, Signal

STRATEGY_REGISTRY: dict[str, type["Strategy"]] = {}


def register(cls: type["Strategy"]) -> type["Strategy"]:
    """Class decorator: makes a Strategy deployable by name from config."""
    if not getattr(cls, "name", None):
        raise ValueError(f"{cls.__qualname__} must define a `name` class var")
    if cls.name in STRATEGY_REGISTRY:
        raise ValueError(f"duplicate strategy name: {cls.name!r}")
    STRATEGY_REGISTRY[cls.name] = cls
    return cls


class Strategy(ABC):
    name: ClassVar[str]
    asset_class: ClassVar[AssetClass]

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        self.params = params or {}

    @abstractmethod
    def on_bar(self, bar: Bar) -> list[Signal]:
        """Called once per completed bar. Return zero or more signals."""

    def on_fill(self, fill: Fill) -> None:
        """Optional: notified when one of this strategy's orders fills."""

    def warmup_bars(self) -> int:
        """How many historical bars this strategy needs before on_bar output counts
        (e.g. the slow period of a moving average). The engine feeds these silently."""
        return 0
