# Goldilocks

A framework for developing, backtesting, paper trading, and (eventually) live-deploying
algorithmic trading strategies across asset classes — forex, crypto, equities, and options.

## Core ideas

- **One `Strategy` interface everywhere.** The same strategy code runs in backtest, paper,
  shadow, and live mode. Strategies emit `Signal`s; they never talk to a broker directly.
- **Brokers are pluggable connectors.** OANDA for forex, Alpaca for equities/crypto/options.
  Adding a broker means implementing one abstract class.
- **Capital allocation is config, not code.** Each deployed strategy has a YAML file in
  `config/strategies/` declaring its bankroll, mode, and risk limits. The engine enforces them.
- **Live trading is opt-in at every layer.** Nothing goes live without explicit flags, and a
  `KILL_SWITCH` file in the repo root halts everything.

## Layout

```
config/
  settings.yaml          global settings (mode defaults, risk limits, data sources)
  strategies/            one YAML per deployed strategy (capital, mode, params)
src/goldilocks/
  core/                  engine, portfolio, risk manager, event/order types
  connectors/            broker abstraction + OANDA/Alpaca implementations
  strategies/            Strategy base + registry, organized by asset class
  backtest/              event-driven replay engine (same Strategy interface)
  data/                  market data adapters (separate from execution)
  monitor/               status view: what's running, capital, P&L, win/loss
  cli.py                 goldilocks run / stop / status / backtest
tests/
notebooks/               research and prototyping
docs/ROADMAP.md          the phased plan
```

## Getting started

```bash
python -m venv .venv
.venv/Scripts/activate          # Windows
pip install -e ".[dev,forex]"
cp .env.example .env            # then fill in your OANDA practice keys
goldilocks --help
```

## Status

Scaffold stage. Interfaces and structure are in place; see [docs/ROADMAP.md](docs/ROADMAP.md)
for what gets built next and [CLAUDE.md](CLAUDE.md) for the conventions this repo follows.

**Nothing in this repo should be pointed at a real-money account yet.**
