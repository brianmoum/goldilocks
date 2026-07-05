"""Load a deployment YAML, fetch data, run the backtest, print the report.

This is what `goldilocks backtest config/strategies/*.yaml` calls. The deployment YAML
gains a `backtest:` section (start/end dates, spread); everything else — strategy,
params, capital limits — is the same config that drives paper/live, parsed by the same
loader (goldilocks.config), so a config can never mean different things per mode.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from goldilocks.backtest.engine import BacktestConfig, BacktestEngine, BacktestResult
from goldilocks.config import load_deployment, load_settings
from goldilocks.data.oanda import OandaDataFeed
from goldilocks.strategies import STRATEGY_REGISTRY


def _parse_date(value: object, name: str) -> datetime:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        dt = datetime.fromisoformat(value)
    else:
        # PyYAML parses bare YYYY-MM-DD as datetime.date
        try:
            dt = datetime.combine(value, datetime.min.time())  # type: ignore[arg-type]
        except TypeError:
            raise ValueError(f"backtest.{name} must be a date, got {value!r}") from None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def run_backtest(
    config_path: Path,
    start: datetime | None = None,
    end: datetime | None = None,
) -> BacktestResult:
    dep = load_deployment(config_path)
    settings = load_settings()

    if dep.strategy not in STRATEGY_REGISTRY:
        raise ValueError(
            f"unknown strategy {dep.strategy!r} — run `goldilocks strategies` to list"
        )
    if len(dep.instruments) != 1:
        raise ValueError("backtest v1 supports exactly one instrument per config")

    bt = dep.backtest
    if start is None:
        if "start" not in bt:
            raise ValueError(f"add a backtest.start date to {config_path} or pass --start")
    start = start or _parse_date(bt["start"], "start")
    end = end or (
        _parse_date(bt["end"], "end") if "end" in bt else datetime.now(tz=UTC)
    )

    engine = BacktestEngine(
        strategy=STRATEGY_REGISTRY[dep.strategy](dep.params),
        config=BacktestConfig(
            allocation=dep.allocation,
            max_position_pct=dep.max_position_pct,
            spread=Decimal(str(bt.get("spread", "0"))),
        ),
    )

    feed = OandaDataFeed(cache_dir=settings.cache_dir)
    bars = feed.get_bars(dep.instruments[0], dep.timeframe, start, end)
    if not bars:
        raise ValueError(
            f"no bars returned for {dep.instruments[0]} {dep.timeframe} "
            f"{start:%Y-%m-%d}..{end:%Y-%m-%d}"
        )
    return engine.run(bars)
