"""TUI dashboard (roadmap phase 3): `goldilocks status`, live. Textual app reading
ONLY the state store, refreshed every few seconds. Quit with q."""

from __future__ import annotations

from pathlib import Path

from goldilocks.monitor.status import sparkline
from goldilocks.store import StateStore

REFRESH_SECONDS = 3.0


def build_status_table(store: StateStore) -> list[tuple[str, ...]]:
    """Rows for the status table — separated from rendering so it's testable."""
    rows = []
    for r in store.status_rows():
        equity_series = [eq for _, eq in store.equity_curve_for(r.strategy_name, 120)]
        rows.append(
            (
                r.strategy_name,
                r.mode,
                f"{r.allocation:.2f}",
                f"{r.exposure:.2f}",
                f"{r.equity:.2f}" if r.equity is not None else "-",
                sparkline(equity_series),
                f"{r.realized_pnl:+.2f}",
                f"{r.wins}/{r.losses}",
                "stopped" if r.stopped else "running",
            )
        )
    return rows


def build_fills_table(store: StateStore, limit: int = 15) -> list[tuple[str, ...]]:
    return [
        (
            f["filled_at"][:16].replace("T", " "),
            f["strategy"],
            f["instrument"],
            f["side"],
            f["quantity"],
            f["price"],
        )
        for f in store.recent_fills(limit=limit)
    ]


def run_tui(db_path: Path) -> None:
    from textual.app import App, ComposeResult
    from textual.widgets import DataTable, Footer, Header

    class GoldilocksTUI(App):
        TITLE = "goldilocks"
        BINDINGS = [("q", "quit", "quit")]
        CSS = "#fills { height: 40%; }"

        def compose(self) -> ComposeResult:
            yield Header(show_clock=True)
            yield DataTable(id="status")
            yield DataTable(id="fills")
            yield Footer()

        def on_mount(self) -> None:
            status = self.query_one("#status", DataTable)
            status.add_columns("strategy", "mode", "alloc", "exposure", "equity",
                               "equity (recent)", "realized", "W/L", "state")
            fills = self.query_one("#fills", DataTable)
            fills.add_columns("time (UTC)", "strategy", "instrument", "side",
                              "qty", "price")
            self.refresh_tables()
            self.set_interval(REFRESH_SECONDS, self.refresh_tables)

        def refresh_tables(self) -> None:
            store = StateStore(db_path)
            try:
                status = self.query_one("#status", DataTable)
                status.clear()
                for row in build_status_table(store):
                    status.add_row(*row)
                fills = self.query_one("#fills", DataTable)
                fills.clear()
                for row in build_fills_table(store):
                    fills.add_row(*row)
            finally:
                store.close()

    GoldilocksTUI().run()
