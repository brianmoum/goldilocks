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

- [x] **Do this first (resolves W1):** extract position sizing + `RiskManager.check_order`
      into one shared component; refactor `BacktestEngine` to route every order through
      it; tests prove backtest and live paths produce identical orders from identical
      signals. Only then build the paper engine on top. *(done 2026-07-05)*
- [x] OANDA connector: account snapshot, market orders (FOK, fills parsed from the
      response), positions; bars via completed-candle polling (tick streaming can
      replace it later without engine changes) *(2026-07-05)*
- [x] Engine run loop: async loop feeding bars to strategies, routing signals through
      the shared RiskManager; SHADOW logs instead of submitting; LIVE triple gate +
      `max_total_live_capital` global cap enforced in the engine *(2026-07-05)*
- [x] RiskManager enforcement: per-strategy capital cap, max position size, daily
      drawdown halt (UTC-day latch), KILL_SWITCH file check before every order
      *(2026-07-05, W1)*
- [x] SQLite state store (WAL): deployments, orders, fills, trades, positions, equity
      per strategy *(2026-07-05)*
- [x] `goldilocks run` / `goldilocks stop` / `goldilocks status` (PID file + SIGTERM;
      status reads ONLY the state store) *(2026-07-05)*
- [x] Crash recovery: on restart, reconcile state store against broker positions —
      mismatches logged, broker adopted as truth, portfolio seeded at broker avg entry
      *(2026-07-05; v1 assumes one strategy per instrument per account)*
- [ ] Validate against the real practice account: `gl run` with the ema_cross paper
      deployment across several M15 bar closes; confirm fills, `goldilocks status`,
      stop/restart reconcile, and a KILL_SWITCH drill

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
- [ ] Slippage/spread model in backtests calibrated from paper/shadow data (resolves W2:
      session-varying spread, slippage, swap/financing on overnight positions, weekend
      gaps, and a margin/leverage model to replace notional-only accounting)
- [ ] Reconnect + resume: engine survives network drops and machine reboots
      (Windows scheduled task or service wrapper)
- [ ] Kill-switch drill: verify KILL_SWITCH halts everything within one tick
- [ ] Live confirmation flow: config `mode: live` + CLI `--live` + typed confirmation
- [ ] Per-strategy graduation criteria: minimum paper-trading duration + performance
      thresholds before live eligibility

## Phase 7 — Portfolio layer

- [ ] Capital reallocation between strategies (manual first, rules later)
- [ ] Cross-strategy exposure limits (e.g. total USD exposure across all strategies)
- [ ] Walk-forward validation tooling as the default backtest mode — **tripwire (W3):
      this jumps the queue and must be built BEFORE the first parameter-tuning or
      strategy-development session, regardless of which phase is in progress**
- [ ] Strategy comparison reports

## Known weaknesses — remediation plan

Identified 2026-07-04 after phase 1. Each item names its trigger: the moment it must be
fixed. Do not fix earlier (premature) or later (retrofit). Check items off here AND at
their phase bullets when resolved.

### W1 — Risk/sizing parity gap

**What:** `BacktestEngine` does its own position sizing privately (`_size`) and never
calls `RiskManager.check_order` (still a stub). The shared-code guarantee currently
covers Strategy and Portfolio, but NOT sizing or risk limits.
**Why it matters:** if the phase 2 live engine grows its own sizing/risk logic, backtest
and live enforce different rules — the exact divergence invariants 2–3 exist to prevent.
Every backtest would then validate behavior the live engine doesn't have.
**Remediation:** first task of phase 2 (see the bullet there): one shared sizing+risk
component, both engines route every order through it, parity proven by tests.
**Trigger:** start of phase 2. Status: **resolved 2026-07-05** — `RiskManager` in
core/risk.py owns `size_signal` + `check_order` (kill switch, daily drawdown halt with
UTC-day latch, position/allocation caps, reduce-only orders exempt from halt/caps);
`BacktestEngine` routes every order through it; the phase 2 engine must do the same.

### W2 — Optimistic market model

**What:** fills always succeed instantly at next bar open ± half a *fixed* spread.
Missing: session-varying spreads (they widen ~10x at news/rollover), slippage, finite
liquidity, swap/financing on overnight positions, weekend gaps, margin/leverage
(accounting is notional-only). Open positions are marked at mid, understating exit cost.
**Why it matters:** every backtest number is systematically inflated — an upper bound,
not an estimate. A strategy that looks marginally profitable (e.g. profit factor 1.08)
is likely a loser net of real costs.
**Remediation:** deliberately deferred to phase 6 — the calibration data (real paper/
shadow fills vs simulator predictions for the same moments) cannot exist before phase 2
runs paper trading. Guessing numbers now would launder "ceiling" into "estimate".
**Interim rule:** read all backtest results as best-case ceilings; no strategy decision
may cite backtest P&L as an expected return.
**Trigger:** phase 6 (needs paper/shadow fill data). Status: open.

### W3 — No overfitting guard (walk-forward missing)

**What:** nothing prevents tuning strategy parameters against one historical window and
shipping the memorized result. CLAUDE.md's backtesting discipline requires walk-forward
(train on one window, score on the next, roll) as the DEFAULT backtest mode; today it
doesn't exist even as an option. Compounding it: the data cache is keyed by exact
(start, end) so exploring windows re-downloads data, and the runner is limited to one
instrument per config, blocking cross-instrument robustness checks.
**Why it matters:** overfitting is the most likely way this project loses real money —
a rigged-by-accident backtest graduates a memorized strategy into live trading.
**Remediation:** walk-forward runner (split → tune → score out-of-sample → roll →
aggregate out-of-sample-only report), cache range reuse, multi-instrument runs. Listed
under phase 7 but NOT gated on it.
**Trigger:** BEFORE the first parameter-tuning or strategy-development session — the
work jumps the queue the day tuning starts. Until then, don't tune parameters against
a single window and trust the result. Status: open.

### W4 — No resilience to transient failures (engine stops on any network blip)

**What:** the engine's loop guard treats every exception as fatal and stops the whole
engine. The bar stream polls OANDA every 5s; over a multi-week paper run a timeout or
5xx is a certainty. Result: engine down until a human notices, with no alerting (phase
3). Related: an outage longer than ~3 bars lost candles silently (poll fetched only
the last 3), gapping strategy indicator state.
**Why it matters:** a paper engine that silently stops is not rehearsing live trading;
uptime is the whole game.
**Remediation:** transient I/O errors (transport errors, 429, 5xx) retried inside the
connector with exponential backoff and never crash the engine; auth/config errors
(4xx) stay fatal; poll size scales with time-since-last-bar so outages backfill missed
candles. Order submission is deliberately NOT retried — a duplicate order is worse
than a dropped one; the failure is logged and recorded in the store.
**Trigger:** before relying on any multi-day paper run. Status: **resolved 2026-07-05**
(connector-level retry/backoff + gap backfill; alerting still lands in phase 3).

### W5 — Risk-critical state does not survive restarts

**What:** the daily drawdown halt latch and day-start equity live only in RiskManager
memory, and the reconciled portfolio resets equity to the full allocation. Restarting
the engine (crash or `goldilocks stop/run`) un-halts a halted strategy and erases the
day's loss from risk math — "turn it off and on again" bypasses the circuit breaker.
**Why it matters:** the halt exists precisely for the moments when something is broken;
those are also the moments restarts happen.
**Remediation:** on startup, rebuild each strategy's day-start equity and halt state
from the state store (equity table already has the history); persist halt events
explicitly. **Trigger:** before trusting the drawdown halt at all — i.e. early phase 3,
alongside the alerting hooks that make halts visible. Status: open.

### W6 — Shadow mode does not simulate fills

**What:** shadow orders are logged but never filled, so the shadow portfolio never
holds a position: exit signals size to zero and are dropped (never logged), and entry
signals repeat instead of being suppressed. The shadow order log is NOT "what live
would have submitted".
**Why it matters:** phase 6 calibrates the backtest cost model by comparing shadow
fills to paper fills, and graduation decisions lean on shadow evidence — both need
shadow to be faithful.
**Remediation:** shadow deployments keep a virtual portfolio filled at the current
price (same simulator as backtest fills), suppressing only the broker submission.
**Trigger:** before shadow output is used for anything (phase 6 gate). Status: open.

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
| 2026-07-04 | Weaknesses tracked in this file (W1–W3) with explicit triggers, not an external tracker | Every session reads this file; tickets outside the repo drift and miss the moment of decision |
