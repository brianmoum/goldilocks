"""Load a deployment YAML, fetch data, run the backtest, print the report.

This is what `goldilocks backtest config/strategies/*.yaml` calls. The deployment YAML
gains a `backtest:` section (start/end dates, spread); everything else — strategy,
params, capital limits — is the same config that will drive paper/live in phase 2.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import yaml

from goldilocks.backtest.engine import BacktestConfig, BacktestEngine, BacktestResult
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
    config = yaml.safe_load(config_path.read_text())

    strategy_name = config["strategy"]
    if strategy_name not in STRATEGY_REGISTRY:
        raise ValueError(
            f"unknown strategy {strategy_name!r} — run `goldilocks strategies` to list"
        )
    instruments = config.get("instruments") or []
    if len(instruments) != 1:
        raise ValueError("backtest v1 supports exactly one instrument per config")
    instrument = instruments[0]

    params = config.get("params") or {}
    timeframe = params.get("timeframe", "M15")
    capital = config.get("capital") or {}
    bt = config.get("backtest") or {}
    if start is None:
        if "start" not in bt:
            raise ValueError(f"add a backtest.start date to {config_path} or pass --start")
    start = start or _parse_date(bt["start"], "start")
    end = end or (
        _parse_date(bt["end"], "end") if "end" in bt else datetime.now(tz=UTC)
    )

    engine = BacktestEngine(
        strategy=STRATEGY_REGISTRY[strategy_name](params),
        config=BacktestConfig(
            allocation=Decimal(str(capital.get("allocation", "1000"))),
            max_position_pct=Decimal(str(capital.get("max_position_pct", "100"))),
            spread=Decimal(str(bt.get("spread", "0"))),
        ),
    )

    feed = OandaDataFeed()
    bars = feed.get_bars(instrument, timeframe, start, end)
    if not bars:
        raise ValueError(
            f"no bars returned for {instrument} {timeframe} {start:%Y-%m-%d}..{end:%Y-%m-%d}"
        )
    return engine.run(bars)
