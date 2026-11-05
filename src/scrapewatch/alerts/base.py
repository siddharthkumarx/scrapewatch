"""Alert payload and dispatch. Env var names and payload: docs/architecture.md "Alerts"."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from scrapewatch.models import Run


class AlertPayload(BaseModel):
    """What every channel receives (the webhook gets it as JSON verbatim)."""

    model_config = ConfigDict(extra="forbid")
    schema_version: int = 1
    run_id: str
    name: str
    verdict: str
    reasons: list[str] = Field(default_factory=list)
    records: int = 0
    usable_records: int = 0
    blocked_share_pct: float = 0.0
    cost_total: float = 0.0
    cost_currency: str = "USD"
    cost_per_usable_record: float | None = None
    report_path: str | None = None
    started_at: str
    finished_at: str | None = None


class Alert(Protocol):
    """One channel."""

    name: str

    def configured(self, env: Mapping[str, str]) -> bool:
        """True when the needed env vars are set."""
        ...

    def send(self, payload: AlertPayload, env: Mapping[str, str]) -> None:
        """Deliver or raise AlertError."""
        ...


def build_payload(run: Run) -> AlertPayload:
    """Reduce a Run to the alert payload."""
    raise NotImplementedError("build-progress item 15")


def send_alerts(
    run: Run, *, on: list[str], channels: list[str] | None, env: Mapping[str, str]
) -> dict[str, str]:
    """Send to configured channels when the verdict is in ``on``.

    Returns channel -> "ok" | error message.
    """
    raise NotImplementedError("build-progress item 15")
