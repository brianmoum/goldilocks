# CLAUDE.md — Goldilocks

Algorithmic trading framework: develop → backtest → paper trade → live deploy strategies
across forex, crypto, equities, and options. Owner: Brian. Real money will eventually flow
through this code — treat every change accordingly.

## Architecture invariants (do not violate)

1. **Strategies never touch brokers.** A `Strategy` consumes market data events and emits
   `Signal`s. Only the engine converts signals to orders and routes them through a
   `BrokerConnector`. If you find yourself importing a connector inside a strategy, stop.
2. **The same Strategy code runs in backtest, paper, shadow, and live.** Never fork a
   strategy into a "backtest version" and a "live version" — fix the engine instead.
3. **Capital limits are enforced by the engine, not trusted to strategies.** The
   `RiskManager` rejects any order that would exceed a strategy's allocated capital,
   position limits, or drawdown halt. Strategies are assumed to be buggy.
4. **Live mode is opt-in at every layer.** `TradingMode.LIVE` requires: `mode: live` in the
   strategy config, AND `--live` on the CLI, AND absence of the `KILL_SWITCH` file. Any new
   code path that can place a real order must check all three. Default mode everywhere is
   `paper`.
5. **Money is `Decimal`, never `float`.** Prices, quantities, balances, P&L.
6. **Secrets live in `.env` only.** Never in YAML config, never committed. `.env.example`
   documents required keys.

## Conventions

- Python 3.11+, `src/` layout, package `goldilocks`. Deps managed in `pyproject.toml` with
  extras per concern (`forex`, `alpaca`, `dashboard`, `dev`).
- New strategies: subclass `goldilocks.strategies.base.Strategy`, set `name` and
  `asset_class` class vars, decorate with `@register`. Place the file under the matching
  asset-class subfolder (`strategies/forex/`, `strategies/crypto/`, ...). Deploy it by
  adding a YAML in `config/strategies/` — no engine changes should be needed.
- New brokers: subclass `goldilocks.connectors.base.BrokerConnector`, declare
  `supports: set[AssetClass]`. The engine picks a connector by asset class + config.
- Engine state (running strategies, allocations, fills, P&L) persists to SQLite in
  `state/` (gitignored). The monitor (CLI, TUI, web) reads **only** this store — never
  query brokers directly from the monitor, so there is one source of truth.
- Timestamps are timezone-aware UTC everywhere. Convert at the display edge only.
- Tests with pytest; lint with ruff. Anything touching order placement needs tests.

## Backtesting discipline

- The backtest engine replays historical bars through the identical Strategy interface.
- Guard against overfitting: strategies should be validated walk-forward (train on one
  window, test on the next, roll). When building validation tooling, make walk-forward the
  default, not an option.
- Backtest fills are optimistic. Model slippage and spread before trusting results, and
  require a paper-trading period before any strategy is eligible for live mode.

## Asset-class gotchas

- **Forex (OANDA):** practice vs live are different hostnames AND different API tokens —
  the connector must never mix them. Units are base-currency units, not lots.
- **Equities (Alpaca):** pattern day trader rule applies under $25k. Market hours only;
  the engine must handle sessions/halts.
- **Crypto (Alpaca):** 24/7 — no session logic, but also no circuit breakers.
- **Options (Alpaca):** multi-leg orders, chains, and greeks need their own data types;
  don't shoehorn them into the spot `Order` model when the time comes (see roadmap phase 5).

## Current status — pick up here

Last session: 2026-07-02. Phase 0 (scaffold) is complete and pushed; nothing beyond it
has started. Interfaces, config schema, CLI skeleton, example EMA-cross forex strategy,
and smoke tests exist; the engine loop, connectors, and backtest replay are stubs raising
`NotImplementedError`.

**Next up: roadmap phase 1** — the forex backtest vertical slice. Concretely:
1. OANDA historical-candles data adapter (`src/goldilocks/data/`, httpx, cache under
   `data/cache/`) — needs OANDA practice keys in `.env` (free demo at oanda.com;
   template in `.env.example`).
2. Backtest engine MVP (`src/goldilocks/backtest/engine.py`) + shared `Portfolio`
   accounting, fills at next bar open with spread applied.
3. Metrics + trade list so `goldilocks backtest config/strategies/ema_cross_eurusd.yaml`
   prints a P&L report.

Environment note: this repo was scaffolded on Windows using the `py` launcher
(Python 3.12). Setup on a fresh machine: `py -m venv .venv`, activate,
`pip install -e ".[dev]"`, then `pytest` (2 tests should pass) and
`goldilocks strategies` to verify.

Build order lives in `docs/ROADMAP.md` — consult it before starting work, and update it
(status + decision log) and this section when finishing a phase or making a design
decision.
