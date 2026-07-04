# Goldilocks Roadmap

Phases are ordered so each one produces something runnable. Update status markers and the
decision log as work lands.

## Phase 0 — Scaffold ✅ (2026-07-02)

Project structure, core types, abstract interfaces (Strategy, BrokerConnector, DataFeed),
strategy registry, config schema, CLI skeleton, safety conventions in CLAUDE.md.

## Phase 1 — Forex vertical slice (backtest) ✅ (2026-07-03)

Goal: `goldilocks backtest config/strategies/ema_cross_eurusd.yaml` produces a P&L report.

- [x] OANDA data adapter: fetch + cache historical candles (practice API, httpx)
- [x] Backtest engine MVP: replay bars → strategy → signals → simulated fills → portfolio
- [x] Fill simulation v1: fill at next bar open, apply the bid/ask spread
- [x] Metrics: total return, max drawdown, win rate, profit factor, trade list
- [x] Validate the example EMA-cross strategy end to end — synthetic data (17k bars,
      full CLI path) on 2026-07-03; real OANDA EUR/USD candles on 2026-07-04 via the
      practice API

## Phase 2 — Paper trading engine

Goal: the same strategy runs live against OANDA's practice account.

- [ ] OANDA connector: account snapshot, streaming prices, order submit/cancel, positions
- [ ] Engine run loop: async event loop feeding bars to strategies, routing signals
- [ ] RiskManager enforcement: per-strategy capital cap, max position size, daily
      drawdown halt, KILL_SWITCH file check before every order
- [ ] SQLite state store: deployments, orders, fills, equity curve per strategy
- [ ] `goldilocks run` / `goldilocks stop` / `goldilocks status` (table: strategy, mode,
      allocated capital, current exposure, open P&L, realized P&L, win/loss record)
- [ ] Crash recovery: on restart, reconcile state store against broker positions

## Phase 3 — Monitoring (TUI + web)

Both read the SQLite state store; neither talks to brokers.

- [ ] TUI dashboard (textual): live-updating version of `goldilocks status` with equity
      sparklines and recent fills
- [ ] Web dashboard (FastAPI + htmx or a small SPA): same data, viewable off-machine
- [ ] Alerting hooks: drawdown halt triggered, connector disconnected, order rejected
      (start with desktop notification / email; keep the hook pluggable)

## Phase 4 — Alpaca + crypto

- [ ] Alpaca connector (paper): equities + crypto
- [ ] Multi-connector engine: strategies routed to the right broker by asset class
- [ ] First crypto strategy under `strategies/crypto/`
- [ ] Market-session handling for equities (open/close, halts)

## Phase 5 — Options support

The hard one — do not start until phases 1–4 are stable.

- [ ] Option-specific data types: contracts, chains, greeks, multi-leg orders
- [ ] Alpaca options data + execution
- [ ] Chain-aware DataFeed interface extension
- [ ] First defined-risk strategy (e.g. vertical spreads) with margin-aware RiskManager

## Phase 6 — Live-money hardening

Gate: no strategy goes live before completing this checklist.

- [ ] Shadow mode: live data, real signal generation, orders logged but NOT sent —
      compare shadow fills vs paper fills to calibrate slippage
- [ ] Slippage/spread model in backtests calibrated from paper/shadow data
- [ ] Reconnect + resume: engine survives network drops and machine reboots
      (Windows scheduled task or service wrapper)
- [ ] Kill-switch drill: verify KILL_SWITCH halts everything within one tick
- [ ] Live confirmation flow: config `mode: live` + CLI `--live` + typed confirmation
- [ ] Per-strategy graduation criteria: minimum paper-trading duration + performance
      thresholds before live eligibility

## Phase 7 — Portfolio layer

- [ ] Capital reallocation between strategies (manual first, rules later)
- [ ] Cross-strategy exposure limits (e.g. total USD exposure across all strategies)
- [ ] Walk-forward validation tooling as the default backtest mode
- [ ] Strategy comparison reports

## Decision log

| Date | Decision | Why |
|------|----------|-----|
| 2026-07-02 | Python 3.11+, src layout | Ecosystem for trading/data; typing support |
| 2026-07-02 | Brokers: Alpaca + OANDA | Alpaca lacks forex; OANDA is the retail forex standard with practice accounts |
| 2026-07-02 | First slice: forex via OANDA | Owner priority; also proves multi-broker abstraction early |
| 2026-07-02 | Own backtest engine, not vectorbt/backtrader | Backtest/live parity: one Strategy interface everywhere |
| 2026-07-02 | Monitoring: CLI + TUI + web, all reading SQLite state store | One source of truth; monitor never queries brokers |
| 2026-07-02 | Money as Decimal | Float rounding is unacceptable for balances/P&L |
| 2026-07-03 | Data feed always hits the OANDA practice host | Candle history is identical on practice/live; backtests must never need live credentials |
| 2026-07-03 | Mid-price candles + configurable spread applied at fill | One request per range instead of bid/ask pairs; spread lives in the deployment YAML (`backtest.spread`) so it's calibratable per instrument in phase 6 |
| 2026-07-03 | Portfolio uses notional cash accounting (buy debits qty×price) | Simple and identical across backtest/live; margin modelling deferred |
| 2026-07-03 | Data cache is CSV keyed by (instrument, timeframe, start, end) | Zero extra deps; swap for parquet if size becomes a problem |
| 2026-07-04 | Secrets injected per-run from Bitwarden via the `gl` shell function | No plaintext token on disk; `.env` stays supported (python-dotenv never overrides preset env vars) |
