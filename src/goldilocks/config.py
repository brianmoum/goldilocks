"""Deployment config loading — one parser for every consumer (backtest runner, engine),
so a YAML can never mean different things in different modes.

Also loads config/settings.yaml (global risk limits, paths). All Decimals arrive as
strings in YAML and stay Decimal here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from goldilocks.core import AssetClass, TradingMode

DEFAULT_TIMEFRAME = "M15"


@dataclass(frozen=True)
class DeploymentConfig:
    path: Path
    strategy: str
    asset_class: AssetClass
    mode: TradingMode
    enabled: bool
    allocation: Decimal
    max_position_pct: Decimal
    instruments: list[str]
    params: dict[str, Any] = field(default_factory=dict)
    backtest: dict[str, Any] = field(default_factory=dict)

    @property
    def timeframe(self) -> str:
        return str(self.params.get("timeframe", DEFAULT_TIMEFRAME))


@dataclass(frozen=True)
class Settings:
    default_mode: TradingMode = TradingMode.PAPER
    max_total_live_capital: Decimal = Decimal(0)
    daily_drawdown_halt_pct: Decimal = Decimal(5)
    kill_switch_file: Path = Path("KILL_SWITCH")
    db_path: Path = Path("state/goldilocks.db")
    cache_dir: Path = Path("data/cache")


def load_deployment(path: Path) -> DeploymentConfig:
    raw = yaml.safe_load(path.read_text())
    capital = raw.get("capital") or {}
    instruments = raw.get("instruments") or []
    if not raw.get("strategy"):
        raise ValueError(f"{path}: missing `strategy`")
    if not instruments:
        raise ValueError(f"{path}: missing `instruments`")
    return DeploymentConfig(
        path=path,
        strategy=raw["strategy"],
        asset_class=AssetClass(raw.get("asset_class", "forex")),
        mode=TradingMode(raw.get("mode", "paper")),
        enabled=bool(raw.get("enabled", True)),
        allocation=Decimal(str(capital.get("allocation", "1000"))),
        max_position_pct=Decimal(str(capital.get("max_position_pct", "100"))),
        instruments=list(instruments),
        params=raw.get("params") or {},
        backtest=raw.get("backtest") or {},
    )


def load_all_deployments(directory: Path) -> list[DeploymentConfig]:
    return [load_deployment(p) for p in sorted(directory.glob("*.yaml"))]


def load_settings(path: Path = Path("config/settings.yaml")) -> Settings:
    if not path.exists():
        return Settings()
    raw = yaml.safe_load(path.read_text()) or {}
    defaults = raw.get("defaults") or {}
    risk = raw.get("risk") or {}
    state = raw.get("state") or {}
    data = raw.get("data") or {}
    return Settings(
        default_mode=TradingMode(defaults.get("mode", "paper")),
        max_total_live_capital=Decimal(str(risk.get("max_total_live_capital", "0"))),
        daily_drawdown_halt_pct=Decimal(str(risk.get("daily_drawdown_halt_pct", "5"))),
        kill_switch_file=Path(risk.get("kill_switch_file", "KILL_SWITCH")),
        db_path=Path(state.get("db_path", "state/goldilocks.db")),
        cache_dir=Path(data.get("cache_dir", "data/cache")),
    )
