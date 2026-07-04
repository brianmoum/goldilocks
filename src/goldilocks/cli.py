"""Goldilocks CLI.

    goldilocks status                      what's running: capital, exposure, P&L, W/L
    goldilocks backtest <strategy.yaml>    run a backtest for one deployment config
    goldilocks run [--live]                start the engine for all enabled deployments
    goldilocks stop                        stop the engine
    goldilocks strategies                  list registered strategies

Live safety (CLAUDE.md invariant 4): `run` only trades live for deployments whose config
says `mode: live` AND when --live is passed AND when no KILL_SWITCH file exists. Without
--live, live-mode deployments are downgraded to shadow with a loud warning.
"""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="goldilocks", description="Algorithmic trading framework")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="show deployed strategies, capital, P&L, win/loss")

    p_backtest = sub.add_parser("backtest", help="backtest one deployment config")
    p_backtest.add_argument("config", help="path to a config/strategies/*.yaml file")
    p_backtest.add_argument("--start", help="override backtest start date (YYYY-MM-DD)")
    p_backtest.add_argument("--end", help="override backtest end date (YYYY-MM-DD)")

    p_run = sub.add_parser("run", help="start the engine for all enabled deployments")
    p_run.add_argument(
        "--live",
        action="store_true",
        help="allow deployments configured with mode: live to trade real money",
    )

    sub.add_parser("stop", help="stop the engine")
    sub.add_parser("strategies", help="list registered strategies")

    args = parser.parse_args(argv)

    if args.command == "strategies":
        from goldilocks.strategies import STRATEGY_REGISTRY

        for name, cls in sorted(STRATEGY_REGISTRY.items()):
            print(f"{name:24} {cls.asset_class.value:10} {cls.__module__}")
        return 0

    if args.command == "backtest":
        from datetime import UTC, datetime
        from pathlib import Path

        from dotenv import load_dotenv

        from goldilocks.backtest.runner import run_backtest

        load_dotenv()

        def parse(value: str | None) -> datetime | None:
            if value is None:
                return None
            dt = datetime.fromisoformat(value)
            return dt if dt.tzinfo else dt.replace(tzinfo=UTC)

        try:
            result = run_backtest(Path(args.config), start=parse(args.start), end=parse(args.end))
        except (ValueError, RuntimeError) as exc:
            print(f"backtest failed: {exc}", file=sys.stderr)
            return 1
        print(result.report())
        return 0

    print(f"goldilocks {args.command}: not implemented yet — see docs/ROADMAP.md", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
