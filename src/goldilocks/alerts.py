"""Alerting hooks (roadmap phase 3): the engine emits, pluggable sinks deliver.

Design rules:
- A broken sink must NEVER break trading: every send is wrapped, failures are logged
  and swallowed.
- The log sink is always on — alerts must exist somewhere greppable even if desktop
  notifications are off or broken.
- New channels (email, webhook, ...) = new AlertSink subclasses; the engine only knows
  the hub.
"""

from __future__ import annotations

import logging
import platform
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

_LEVELS = {"info": logging.INFO, "warning": logging.WARNING, "critical": logging.CRITICAL}


@dataclass(frozen=True, slots=True)
class Alert:
    severity: str        # "info" | "warning" | "critical"
    strategy: str
    message: str
    at: datetime


class AlertSink(ABC):
    @abstractmethod
    def send(self, alert: Alert) -> None: ...


class LogSink(AlertSink):
    def send(self, alert: Alert) -> None:
        logger.log(
            _LEVELS.get(alert.severity, logging.WARNING),
            "ALERT [%s] %s: %s", alert.severity, alert.strategy, alert.message,
        )


class DesktopSink(AlertSink):
    """macOS notification center via osascript; silently unavailable elsewhere
    (Linux/Windows delivery can land later without touching the engine)."""

    def send(self, alert: Alert) -> None:
        if platform.system() != "Darwin":
            return
        title = f"goldilocks: {alert.strategy}"
        message = alert.message.replace('"', "'")
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{message}" with title "{title.replace(chr(34), chr(39))}"'],
            check=False,
            capture_output=True,
            timeout=5,
        )


class AlertHub:
    def __init__(self, sinks: list[AlertSink] | None = None) -> None:
        self.sinks: list[AlertSink] = sinks if sinks is not None else [LogSink()]

    def emit(
        self, severity: str, strategy: str, message: str, at: datetime | None = None
    ) -> None:
        alert = Alert(severity, strategy, message, at or datetime.now(tz=UTC))
        for sink in self.sinks:
            try:
                sink.send(alert)
            except Exception:
                logger.exception("alert sink %s failed (alert: %s)",
                                 type(sink).__name__, alert.message)
