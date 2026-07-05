"""SQLite state store — the engine's persistent memory and the monitor's ONLY data
source (CLAUDE.md: the monitor never queries brokers, so there is one source of truth).

Written by the engine as things happen: deployments, orders, fills, closed trades,
open positions, equity snapshots. Read by `goldilocks status` (and later the TUI/web
dashboards) from a separate process — WAL mode makes concurrent reads safe.

Storage conventions: Decimals as TEXT (exact), timestamps as ISO-8601 UTC TEXT.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from goldilocks.core import Fill, Order, Position
from goldilocks.core.portfolio import Trade

_SCHEMA = """
CREATE TABLE IF NOT EXISTS deployments (
    strategy_name TEXT PRIMARY KEY,
    asset_class   TEXT NOT NULL,
    mode          TEXT NOT NULL,
    allocation    TEXT NOT NULL,
    config_path   TEXT NOT NULL,
    started_at    TEXT,
    stopped_at    TEXT
);
CREATE TABLE IF NOT EXISTS orders (
    order_id      TEXT PRIMARY KEY,
    strategy_name TEXT NOT NULL,
    instrument    TEXT NOT NULL,
    side          TEXT NOT NULL,
    quantity      TEXT NOT NULL,
    mode          TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    limit_price   TEXT,
    status        TEXT NOT NULL,
    reason        TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS fills (
    order_id      TEXT NOT NULL,
    strategy_name TEXT NOT NULL,
    instrument    TEXT NOT NULL,
    side          TEXT NOT NULL,
    quantity      TEXT NOT NULL,
    price         TEXT NOT NULL,
    filled_at     TEXT NOT NULL,
    commission    TEXT NOT NULL DEFAULT '0'
);
CREATE TABLE IF NOT EXISTS trades (
    strategy_name TEXT NOT NULL,
    instrument    TEXT NOT NULL,
    side          TEXT NOT NULL,
    quantity      TEXT NOT NULL,
    entry_price   TEXT NOT NULL,
    exit_price    TEXT NOT NULL,
    opened_at     TEXT NOT NULL,
    closed_at     TEXT NOT NULL,
    pnl           TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS positions (
    strategy_name   TEXT NOT NULL,
    instrument      TEXT NOT NULL,
    quantity        TEXT NOT NULL,
    avg_entry_price TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    PRIMARY KEY (strategy_name, instrument)
);
CREATE TABLE IF NOT EXISTS equity (
    strategy_name TEXT NOT NULL,
    taken_at      TEXT NOT NULL,
    equity        TEXT NOT NULL,
    PRIMARY KEY (strategy_name, taken_at)
);
"""


@dataclass(frozen=True)
class StatusRow:
    strategy_name: str
    mode: str
    allocation: Decimal
    exposure: Decimal          # sum of |qty * avg_entry| over open positions
    equity: Decimal | None     # latest snapshot
    realized_pnl: Decimal
    wins: int
    losses: int
    stopped: bool


class StateStore:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # --- writes (engine only) ---

    def record_deployment(
        self,
        strategy_name: str,
        asset_class: str,
        mode: str,
        allocation: Decimal,
        config_path: str,
        started_at: datetime,
    ) -> None:
        self._conn.execute(
            "INSERT INTO deployments VALUES (?,?,?,?,?,?,NULL) "
            "ON CONFLICT(strategy_name) DO UPDATE SET asset_class=excluded.asset_class,"
            "mode=excluded.mode, allocation=excluded.allocation,"
            "config_path=excluded.config_path, started_at=excluded.started_at,"
            "stopped_at=NULL",
            (strategy_name, asset_class, mode, str(allocation), config_path,
             started_at.isoformat()),
        )
        self._conn.commit()

    def mark_stopped(self, strategy_name: str, at: datetime) -> None:
        self._conn.execute(
            "UPDATE deployments SET stopped_at=? WHERE strategy_name=?",
            (at.isoformat(), strategy_name),
        )
        self._conn.commit()

    def record_order(self, order: Order, status: str, reason: str = "") -> None:
        self._conn.execute(
            "INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                order.order_id, order.strategy_name, order.instrument,
                order.side.value, str(order.quantity), order.mode.value,
                order.created_at.isoformat(),
                str(order.limit_price) if order.limit_price is not None else None,
                status, reason,
            ),
        )
        self._conn.commit()

    def update_order_status(self, order_id: str, status: str, reason: str = "") -> None:
        self._conn.execute(
            "UPDATE orders SET status=?, reason=? WHERE order_id=?",
            (status, reason, order_id),
        )
        self._conn.commit()

    def record_fill(self, fill: Fill, strategy_name: str) -> None:
        self._conn.execute(
            "INSERT INTO fills VALUES (?,?,?,?,?,?,?,?)",
            (
                fill.order_id, strategy_name, fill.instrument, fill.side.value,
                str(fill.quantity), str(fill.price), fill.filled_at.isoformat(),
                str(fill.commission),
            ),
        )
        self._conn.commit()

    def record_trade(self, trade: Trade, strategy_name: str) -> None:
        self._conn.execute(
            "INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?)",
            (
                strategy_name, trade.instrument, trade.side.value,
                str(trade.quantity), str(trade.entry_price), str(trade.exit_price),
                trade.opened_at.isoformat(), trade.closed_at.isoformat(),
                str(trade.pnl),
            ),
        )
        self._conn.commit()

    def replace_positions(
        self, strategy_name: str, positions: list[Position], at: datetime
    ) -> None:
        self._conn.execute(
            "DELETE FROM positions WHERE strategy_name=?", (strategy_name,)
        )
        self._conn.executemany(
            "INSERT INTO positions VALUES (?,?,?,?,?)",
            [
                (strategy_name, p.instrument, str(p.quantity),
                 str(p.avg_entry_price), at.isoformat())
                for p in positions
            ],
        )
        self._conn.commit()

    def record_equity(self, strategy_name: str, at: datetime, equity: Decimal) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO equity VALUES (?,?,?)",
            (strategy_name, at.isoformat(), str(equity)),
        )
        self._conn.commit()

    # --- reads (monitor) ---

    def positions_for(self, strategy_name: str) -> list[Position]:
        rows = self._conn.execute(
            "SELECT * FROM positions WHERE strategy_name=?", (strategy_name,)
        ).fetchall()
        return [
            Position(
                instrument=r["instrument"],
                quantity=Decimal(r["quantity"]),
                avg_entry_price=Decimal(r["avg_entry_price"]),
            )
            for r in rows
        ]

    def status_rows(self) -> list[StatusRow]:
        deployments = self._conn.execute(
            "SELECT * FROM deployments ORDER BY strategy_name"
        ).fetchall()
        out = []
        for d in deployments:
            name = d["strategy_name"]
            trade_rows = self._conn.execute(
                "SELECT pnl FROM trades WHERE strategy_name=?", (name,)
            ).fetchall()
            pnls = [Decimal(r["pnl"]) for r in trade_rows]
            exposure = sum(
                (abs(p.quantity * p.avg_entry_price) for p in self.positions_for(name)),
                Decimal(0),
            )
            eq_row = self._conn.execute(
                "SELECT equity FROM equity WHERE strategy_name=? "
                "ORDER BY taken_at DESC LIMIT 1",
                (name,),
            ).fetchone()
            out.append(
                StatusRow(
                    strategy_name=name,
                    mode=d["mode"],
                    allocation=Decimal(d["allocation"]),
                    exposure=exposure,
                    equity=Decimal(eq_row["equity"]) if eq_row else None,
                    realized_pnl=sum(pnls, Decimal(0)),
                    wins=sum(1 for p in pnls if p > 0),
                    losses=sum(1 for p in pnls if p < 0),
                    stopped=d["stopped_at"] is not None,
                )
            )
        return out
