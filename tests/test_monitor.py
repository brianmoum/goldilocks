"""Monitor surfaces: sparkline rendering, TUI data assembly, web API. All of them
read only the state store."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient

from goldilocks.core import Fill, OrderSide, Position
from goldilocks.monitor.status import sparkline
from goldilocks.monitor.tui import build_fills_table, build_status_table
from goldilocks.monitor.web import create_app
from goldilocks.store import StateStore

T0 = datetime(2026, 7, 6, 10, 0, tzinfo=UTC)


def seeded_store(tmp_path) -> StateStore:
    store = StateStore(tmp_path / "state" / "test.db")
    store.record_deployment(
        "ema_cross", "forex", "paper", Decimal("1000"), "config/x.yaml", T0
    )
    store.replace_positions(
        "ema_cross", [Position("EUR_USD", Decimal(400), Decimal("1.1000"))], T0
    )
    for i, eq in enumerate(("1000", "1004", "998", "1010")):
        store.record_equity("ema_cross", T0 + timedelta(minutes=15 * i), Decimal(eq))
    store.record_fill(
        Fill("o1", "EUR_USD", OrderSide.BUY, Decimal(400), Decimal("1.1000"), T0),
        "ema_cross",
    )
    return store


# --- sparkline ---


def test_sparkline_shape_and_range():
    values = [Decimal(v) for v in ("1", "2", "3", "4", "5")]
    s = sparkline(values)
    assert len(s) == 5
    assert s[0] == "▁" and s[-1] == "█"


def test_sparkline_flat_and_empty():
    assert sparkline([]) == ""
    assert sparkline([Decimal(5), Decimal(5)]) == "▁▁"


def test_sparkline_downsamples():
    values = [Decimal(i) for i in range(1000)]
    assert len(sparkline(values, width=24)) == 24


# --- TUI data assembly ---


def test_build_status_table(tmp_path):
    (row,) = build_status_table(seeded_store(tmp_path))
    assert row[0] == "ema_cross"
    assert row[1] == "paper"
    assert row[4] == "1010.00"          # latest equity
    assert len(row[5]) > 0              # sparkline present
    assert row[8] == "running"


def test_build_fills_table(tmp_path):
    (row,) = build_fills_table(seeded_store(tmp_path))
    assert row[1] == "ema_cross"
    assert row[3] == "buy"


# --- web API ---


def test_web_api(tmp_path):
    seeded_store(tmp_path)
    client = TestClient(create_app(tmp_path / "state" / "test.db"))

    (status,) = client.get("/api/status").json()
    assert status["strategy"] == "ema_cross"
    assert status["equity"] == "1010"
    assert status["exposure"] == "440.0000"  # Decimal survives as string

    equity = client.get("/api/equity/ema_cross").json()
    assert [e["equity"] for e in equity] == ["1000", "1004", "998", "1010"]

    assert client.get("/api/equity/nope").status_code == 404

    (fill,) = client.get("/api/fills").json()
    assert fill["instrument"] == "EUR_USD"

    page = client.get("/")
    assert page.status_code == 200
    assert "goldilocks" in page.text
