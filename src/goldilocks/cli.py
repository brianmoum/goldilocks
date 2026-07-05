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

_HELP = """\
goldilocks — algorithmic trading framework

usage: goldilocks <command> [options]      (or `gl <command>` — same CLI, with
                                            secrets injected from Bitwarden and
                                            run from the repo root)

commands:
  strategies
      List registered strategies: name, asset class, module.

  backtest <config.yaml> [--start YYYY-MM-DD] [--end YYYY-MM-DD]
      Replay history through one deployment config and print the P&L report
      (return, max drawdown, win rate, profit factor, trade list). Dates come
      from the YAML's `backtest:` section unless overridden. Fills are
      simulated at next bar open with the configured spread; results are
      best-case ceilings, not estimates (see ROADMAP W2).

  run [--live]
      Start the engine in the FOREGROUND for every enabled deployment in
      config/strategies/. Streams bars, routes signals through the shared
      RiskManager, submits paper/live orders, records everything to the state
      store. Stop with Ctrl-C or `goldilocks stop` from another terminal.
      --live is one of THREE required gates for real-money trading: a
      deployment also needs `mode: live` in its YAML, no KILL_SWITCH file may
      exist, and its allocation must fit max_total_live_capital in
      config/settings.yaml. Anything failing a gate runs in shadow instead.

  stop
      Stop a running engine cleanly (reads state/engine.pid, sends SIGTERM).

  status
      Show the deployment table: mode, allocation, exposure, equity, realized
      P&L, win/loss record, running/stopped. Reads ONLY the SQLite state
      store — never the broker.

  help
      This overview. `goldilocks <command> --help` shows per-command flags.

emergency halt (not a command):
  `touch KILL_SWITCH` in the repo root blocks every order from every strategy
  (backtests excepted) until the file is deleted.
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="goldilocks", description="Algorithmic trading framework")
    sub = parser.add_subparsers(dest="command")

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
    sub.add_parser("help", help="overview of all commands")

    args = parser.parse_args(argv)

    if args.command is None or args.command == "help":
        print(_HELP)
        return 0

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

    if args.command == "run":
        return _cmd_run(live=args.live)
    if args.command == "status":
        return _cmd_status()
    if args.command == "stop":
        return _cmd_stop()

    print(f"goldilocks {args.command}: not implemented yet — see docs/ROADMAP.md", file=sys.stderr)
    return 1


_PID_FILE = "state/engine.pid"


def _cmd_run(live: bool) -> int:
    import asyncio
    import logging
    import os
    import signal
    from pathlib import Path

    from dotenv import load_dotenv

    from goldilocks.config import load_all_deployments, load_settings
    from goldilocks.core.engine import Engine

    load_dotenv()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s"
    )
    settings = load_settings()
    try:
        deployments = load_all_deployments(Path("config/strategies"))
        engine = Engine(settings, deployments, live=live)
    except (ValueError, RuntimeError) as exc:
        print(f"cannot start engine: {exc}", file=sys.stderr)
        return 1

    pid_file = Path(_PID_FILE)
    if pid_file.exists():
        print(f"{pid_file} exists — is the engine already running? "
              f"(goldilocks stop, or delete the file if stale)", file=sys.stderr)
        return 1
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(os.getpid()))

    async def main() -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, engine.stop)
        await engine.run()

    try:
        asyncio.run(main())
    finally:
        pid_file.unlink(missing_ok=True)
    return 0


def _cmd_stop() -> int:
    import os
    import signal
    from pathlib import Path

    pid_file = Path(_PID_FILE)
    if not pid_file.exists():
        print("engine is not running (no PID file)", file=sys.stderr)
        return 1
    pid = int(pid_file.read_text().strip())
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        print(f"stale PID file (process {pid} not found) — removing", file=sys.stderr)
        pid_file.unlink()
        return 1
    print(f"sent SIGTERM to engine (pid {pid})")
    return 0


def _cmd_status() -> int:
    from goldilocks.config import load_settings
    from goldilocks.store import StateStore

    settings = load_settings()
    if not settings.db_path.exists():
        print("no state store yet — nothing has run")
        return 0
    rows = StateStore(settings.db_path).status_rows()
    if not rows:
        print("no deployments recorded")
        return 0
    header = (f"{'strategy':<20} {'mode':<7} {'alloc':>10} {'exposure':>10} "
              f"{'equity':>10} {'realized':>10} {'W/L':>7}  state")
    print(header)
    print("-" * len(header))
    for r in rows:
        equity = f"{r.equity:.2f}" if r.equity is not None else "-"
        print(
            f"{r.strategy_name:<20} {r.mode:<7} {r.allocation:>10.2f} "
            f"{r.exposure:>10.2f} {equity:>10} {r.realized_pnl:>+10.2f} "
            f"{r.wins:>3}/{r.losses:<3}  {'stopped' if r.stopped else 'running'}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
