"""Alert hub + engine emission points. The invariant that matters: a broken sink can
never break trading, and the engine's safety events always reach the hub."""

import asyncio
from decimal import Decimal

from test_engine import make_bar, make_engine

from goldilocks.alerts import Alert, AlertHub, AlertSink, LogSink
from goldilocks.config import Settings
from goldilocks.core import OrderSide, Signal


class CaptureSink(AlertSink):
    def __init__(self):
        self.alerts: list[Alert] = []

    def send(self, alert: Alert) -> None:
        self.alerts.append(alert)


class ExplodingSink(AlertSink):
    def send(self, alert: Alert) -> None:
        raise RuntimeError("sink is broken")


def test_broken_sink_never_propagates():
    capture = CaptureSink()
    hub = AlertHub([ExplodingSink(), capture])
    hub.emit("critical", "s", "message")  # must not raise
    assert len(capture.alerts) == 1  # later sinks still receive


def test_default_hub_has_log_sink():
    assert any(isinstance(s, LogSink) for s in AlertHub().sinks)


def test_emit_stamps_time_when_omitted():
    capture = CaptureSink()
    AlertHub([capture]).emit("info", "s", "m")
    assert capture.alerts[0].at.tzinfo is not None


def test_crash_alert_names_the_open_position(tmp_path):
    """W7: an engine that dies holding a position must say so. Nothing manages that
    exposure until a human acts, so 'stopped' and 'stopped while long' are different
    emergencies and the alert has to distinguish them."""
    engine, connector = make_engine(tmp_path)
    capture = CaptureSink()
    engine.alerts = AlertHub([capture])

    async def run_all():
        task = asyncio.create_task(engine.run())
        await asyncio.sleep(0.02)
        dep = engine._deployments[0]
        dep.strategy.next_signals = [Signal("EUR_USD", OrderSide.BUY)]
        connector.bar_queue.put_nowait(make_bar("1.1000"))
        await asyncio.sleep(0.05)
        assert dep.portfolio.position("EUR_USD") is not None, "setup: entry never filled"
        connector.bar_stream_error = RuntimeError("401 Unauthorized")
        connector.bar_queue.put_nowait(make_bar("1.1000", 15))
        await asyncio.wait_for(task, timeout=2)

    asyncio.run(run_all())
    crash = [a for a in capture.alerts if "ENGINE STOPPED" in a.message]
    assert crash, f"no crash alert; got: {[a.message for a in capture.alerts]}"
    assert crash[0].severity == "critical"
    assert "POSITION STILL OPEN" in crash[0].message
    assert "EUR_USD" in crash[0].message


def test_crash_alert_says_flat_when_no_position(tmp_path):
    engine, connector = make_engine(tmp_path)
    capture = CaptureSink()
    engine.alerts = AlertHub([capture])

    async def run_all():
        task = asyncio.create_task(engine.run())
        await asyncio.sleep(0.02)
        connector.bar_stream_error = RuntimeError("boom")
        connector.bar_queue.put_nowait(make_bar("1.1000"))
        await asyncio.wait_for(task, timeout=2)

    asyncio.run(run_all())
    crash = [a for a in capture.alerts if "ENGINE STOPPED" in a.message]
    assert crash and "(flat)" in crash[0].message


def test_engine_emits_on_rejection_and_halt(tmp_path):
    settings = Settings(
        db_path=tmp_path / "state" / "test.db",
        kill_switch_file=tmp_path / "KILL_SWITCH",
        daily_drawdown_halt_pct=Decimal(5),
    )
    engine, connector = make_engine(tmp_path, settings=settings)
    capture = CaptureSink()
    engine.alerts = AlertHub([capture])

    async def run_all():
        task = asyncio.create_task(engine.run())
        await asyncio.sleep(0.02)
        dep = engine._deployments[0]
        dep.strategy.next_signals = [Signal("EUR_USD", OrderSide.BUY)]
        connector.bar_queue.put_nowait(make_bar("1.1000"))
        await asyncio.sleep(0.02)
        connector.bar_queue.put_nowait(make_bar("1.1000", 15))
        await asyncio.sleep(0.02)
        # Price collapses: halt fires on this bar.
        connector.bar_queue.put_nowait(make_bar("0.9000", 30))
        await asyncio.sleep(0.02)
        # Entry attempt while halted: rejected -> warning alert.
        dep.strategy.next_signals = [Signal("EUR_USD", OrderSide.SELL)]
        connector.bar_queue.put_nowait(make_bar("0.9000", 45))
        await asyncio.sleep(0.02)
        engine.stop()
        await asyncio.wait_for(task, timeout=2)

    asyncio.run(run_all())
    severities = [a.severity for a in capture.alerts]
    messages = " | ".join(a.message for a in capture.alerts)
    assert "critical" in severities, f"halt alert missing; got: {messages}"
    assert "drawdown halt" in messages
    assert "warning" in severities, f"rejection alert missing; got: {messages}"
    assert "order rejected" in messages
