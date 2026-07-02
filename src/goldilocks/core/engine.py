"""Trading engine: wires strategies, connectors, risk, and the state store together.

Responsibilities (roadmap phase 2):
- Load deployments from config/strategies/*.yaml, instantiate strategies from the registry.
- Feed bars from connectors to strategies; convert Signals to Orders (sizing from the
  strategy's allocation and signal strength).
- Route every order through RiskManager.check_order(). Rejections are logged, not fatal.
- SHADOW mode: run everything, log the order, do NOT call connector.submit_order().
- LIVE mode requires config `mode: live` + CLI `--live` + no KILL_SWITCH (CLAUDE.md
  invariant 4) — assert all three here even though the CLI also checks.
- Persist orders, fills, and equity snapshots to the SQLite state store; the monitor
  reads only that store.
- On startup, reconcile the state store against broker positions (crash recovery).
"""

from __future__ import annotations


class Engine:
    def __init__(self) -> None:
        raise NotImplementedError("roadmap phase 2")
