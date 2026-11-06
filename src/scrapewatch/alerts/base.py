"""Alert payload and dispatch. Env var names and payload: docs/architecture.md "Alerts".

Only this package performs network I/O, through ``urllib.request`` and ``smtplib`` from the
standard library. Credentials are read from the environment at send time and never appear in
messages, logs or errors (URLs carrying a token are redacted from error text).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from scrapewatch.errors import AlertError
from scrapewatch.models import Run, VerdictStatus, per_1000

TIMEOUT_SECONDS = 10
CHANNEL_ORDER = ("telegram", "slack", "email", "webhook")
VERDICTS = tuple(v.value for v in VerdictStatus)
_SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥", "INR": "₹"}


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
    cost_per_1000_usable_records: float | None = None  # set when per usable record < 0.01
    report_path: str | None = None
    started_at: str
    finished_at: str | None = None


class Alert(Protocol):
    """One channel."""

    name: str

    def configured(self, env: Mapping[str, str]) -> bool:
        """True when the needed env vars are set."""
        ...  # pragma: no cover

    def send(self, payload: AlertPayload, env: Mapping[str, str]) -> None:
        """Deliver or raise AlertError."""
        ...  # pragma: no cover


def build_payload(run: Run) -> AlertPayload:
    """Reduce a Run to the alert payload."""
    c = run.counts
    share = 100.0 * c.bad_documents / c.document_responses if c.document_responses else 0.0
    return AlertPayload(
        run_id=run.run_id,
        name=run.name,
        verdict=run.verdict.status.value,
        reasons=list(run.verdict.reasons),
        records=c.records,
        usable_records=c.usable_records,
        blocked_share_pct=round(share, 2),
        cost_total=run.cost.total,
        cost_currency=run.cost.currency,
        cost_per_usable_record=run.cost.per_usable_record,
        cost_per_1000_usable_records=per_1000(run.cost.per_usable_record),
        report_path=run.report_path,
        started_at=run.started_at.isoformat().replace("+00:00", "Z"),
        finished_at=None
        if run.finished_at is None
        else run.finished_at.isoformat().replace("+00:00", "Z"),
    )


def money(value: float | None, currency: str) -> str:
    """Rounded for people: ``$0.41``, ``$0.0115``, ``< $0.0001``."""
    if value is None:
        return "-"
    sym = _SYMBOLS.get(currency, currency + " ")
    if value == 0:
        return f"{sym}0"
    if 0 < abs(value) < 0.0001:
        return f"< {sym}0.0001"
    if abs(value) >= 0.1:
        return f"{sym}{value:,.2f}"
    return f"{sym}{value:.4f}" if abs(value) >= 0.01 else f"{sym}{value:.6f}".rstrip("0")


def cost_headline(payload: AlertPayload) -> str:
    """Cost per usable record, or per 1,000 usable records when it is below 0.01."""
    cur = payload.cost_currency
    if payload.cost_per_1000_usable_records is not None:
        return f"{money(payload.cost_per_1000_usable_records, cur)} per 1,000 usable records"
    if payload.cost_per_usable_record is not None:
        return f"{money(payload.cost_per_usable_record, cur)} per usable record"
    return "no usable records"


def message_lines(payload: AlertPayload) -> list[str]:
    """The four lines every channel sends (architecture 13.4)."""
    reasons = " ".join(payload.reasons) or "All checks passed."
    return [
        f"scrapewatch · {payload.name} · {payload.verdict.upper()}",
        reasons,
        f"Records {payload.records:,} (usable {payload.usable_records:,}) · cost "
        f"{money(payload.cost_total, payload.cost_currency)} · {cost_headline(payload)}",
        f"Report: {payload.report_path or '(none written)'}",
    ]


def message_text(payload: AlertPayload) -> str:
    """Plain-text message."""
    return "\n".join(message_lines(payload))


def post_json(
    url: str,
    body: bytes,
    *,
    headers: Mapping[str, str] | None = None,
    what: str,
) -> bytes:
    """POST ``body`` as JSON; raise AlertError with ``what`` (never the URL) on failure."""
    request = urllib.request.Request(  # noqa: S310 - https URLs from the user's own env vars
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", **dict(headers or {})},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
            data: bytes = response.read()
            return data
    except urllib.error.HTTPError as exc:
        raise AlertError(f"{what}: HTTP {exc.code}") from None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        reason = getattr(exc, "reason", exc)
        raise AlertError(f"{what}: {type(exc).__name__}: {reason}") from None


def dumps(data: Any) -> bytes:
    """Compact, stable JSON bytes."""
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()


def channels() -> dict[str, Alert]:
    """Every channel by name."""
    from scrapewatch.alerts.email import EmailAlert
    from scrapewatch.alerts.slack import SlackAlert
    from scrapewatch.alerts.telegram import TelegramAlert
    from scrapewatch.alerts.webhook import WebhookAlert

    return {
        "telegram": TelegramAlert(),
        "slack": SlackAlert(),
        "email": EmailAlert(),
        "webhook": WebhookAlert(),
    }


def alert_on(on: list[str], env: Mapping[str, str]) -> list[str]:
    """``SCRAPEWATCH_ALERT_ON`` (comma list) overrides ``alerts.on``."""
    override = env.get("SCRAPEWATCH_ALERT_ON")
    if override is not None:
        return [v.strip().lower() for v in override.split(",") if v.strip()]
    return [v.lower() for v in on]


def deliver(
    payload: AlertPayload, *, channels_wanted: list[str] | None, env: Mapping[str, str]
) -> dict[str, str]:
    """Send ``payload`` to the wanted channels (empty or None: every configured one)."""
    available = channels()
    names = (
        list(channels_wanted)
        if channels_wanted
        else [n for n in CHANNEL_ORDER if available[n].configured(env)]
    )
    results: dict[str, str] = {}
    for name in names:
        channel = available.get(name)
        if channel is None:
            results[name] = f"unknown channel (use {', '.join(CHANNEL_ORDER)})"
            continue
        if not channel.configured(env):
            results[name] = "not configured (environment variables missing)"
            continue
        try:
            channel.send(payload, env)
        except AlertError as exc:
            results[name] = str(exc)
        except Exception as exc:  # a channel bug must not escape finish()
            results[name] = f"{type(exc).__name__}: {exc}"
        else:
            results[name] = "ok"
    return results


def send_alerts(
    run: Run,
    *,
    on: list[str],
    channels: list[str] | None,
    env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Send to configured channels when the verdict is in ``on``.

    Returns channel -> "ok" | error message. Never raises.
    """
    env = os.environ if env is None else env
    if run.verdict.status.value not in alert_on(on, env):
        return {}
    return deliver(build_payload(run), channels_wanted=channels, env=env)
