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
6. **Secrets arrive via environment variables only.** Never in YAML config, never
   committed. `.env.example` documents required keys. Preferred injection: the `gl`
   shell function (in `~/.zshrc`) pulls them from Bitwarden per invocation; a
   gitignored `.env` (auto-loaded via python-dotenv, which never overrides already-set
   env vars) also works.

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

Last session: 2026-07-05. **Phases 1–3 built.** Phase 2 (paper engine): shared
`RiskManager` (W1), `OandaConnector` (market orders, candle-polling bar stream with
retry/backoff + outage backfill (W4), strict practice/live credential isolation),
async `Engine` (SHADOW logging, LIVE triple gate + global capital cap), SQLite state
store, `goldilocks run/stop/status`. Phase 3 (monitoring): W5 resolved (restart
rebuilds portfolio from stored fills and re-arms the day's halt state), pluggable
alerting (log + macOS desktop; halt/rejection/crash events), `goldilocks tui`
(textual, sparklines) and `goldilocks web` (FastAPI, localhost, no auth). 74 tests
pass; ruff clean.

**Next up:** (a) finish the phase 2 real-account validation checkbox (`gl run` over
several M15 closes: fills, status, stop/restart reconcile, KILL_SWITCH drill — now
also exercising the dashboards); (b) then phase 4 (Alpaca + crypto) or a persistent
runtime (launchd/systemd or a small always-on box — W5 being fixed makes
auto-restart safe now). W2, W3, W6 remain open in docs/ROADMAP.md with triggers.

Environment note: originally scaffolded on Windows (`py` launcher, Python 3.12); now
also developed on macOS (`python3 -m venv .venv`, Python 3.14). Both work. Setup on a
fresh machine: create a venv, `pip install -e ".[dev]"`, then `pytest` and
`goldilocks strategies` to verify.

Build order lives in `docs/ROADMAP.md` — consult it before starting work, and update it
(status + decision log) and this section when finishing a phase or making a design
decision.
