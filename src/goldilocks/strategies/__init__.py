"""Strategy package. Importing asset-class submodules triggers @register decorators,
so the registry is populated by importing this package."""

from goldilocks.strategies.base import STRATEGY_REGISTRY, Strategy, register
from goldilocks.strategies import forex  # noqa: F401  (registers forex strategies)

__all__ = ["STRATEGY_REGISTRY", "Strategy", "register"]
