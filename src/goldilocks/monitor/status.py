"""At-a-glance view of everything deployed. Reads ONLY the SQLite state store — never
brokers — so CLI, TUI, and web all show the same truth (CLAUDE.md convention).

Per strategy instance: name, asset class, mode (paper/shadow/live), enabled/halted,
allocated capital, current exposure, open P&L, realized P&L, win/loss record, last signal.

`goldilocks status` prints the table (roadmap phase 2); the textual TUI and FastAPI web
dashboard (roadmap phase 3) render the same query.
"""

from __future__ import annotations


def status_table() -> str:
    raise NotImplementedError("roadmap phase 2")
