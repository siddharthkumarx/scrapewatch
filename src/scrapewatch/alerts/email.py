"""email alert channel (stdlib SMTP only): plain text, the HTML report attached when small."""

from __future__ import annotations

import smtplib
from collections.abc import Mapping
from email.message import EmailMessage
from pathlib import Path

from scrapewatch.alerts.base import TIMEOUT_SECONDS, AlertPayload, message_lines, message_text
from scrapewatch.errors import AlertError

ATTACH_MAX_BYTES = 2 * 1024 * 1024
_REQUIRED = ("SCRAPEWATCH_SMTP_HOST", "SCRAPEWATCH_EMAIL_FROM", "SCRAPEWATCH_EMAIL_TO")


def build_message(payload: AlertPayload, env: Mapping[str, str]) -> EmailMessage:
    """The email: subject is the first message line, the report attached if under 2 MiB."""
    msg = EmailMessage()
    msg["Subject"] = message_lines(payload)[0]
    msg["From"] = env["SCRAPEWATCH_EMAIL_FROM"]
    msg["To"] = ", ".join(a.strip() for a in env["SCRAPEWATCH_EMAIL_TO"].split(",") if a.strip())
    msg.set_content(message_text(payload) + "\n")
    report = Path(payload.report_path) if payload.report_path else None
    if report is not None and report.is_file() and report.stat().st_size <= ATTACH_MAX_BYTES:
        msg.add_attachment(
            report.read_bytes(), maintype="text", subtype="html", filename=report.name
        )
    return msg


class EmailAlert:
    """``SCRAPEWATCH_SMTP_*`` and ``SCRAPEWATCH_EMAIL_FROM`` / ``SCRAPEWATCH_EMAIL_TO``."""

    name = "email"

    def configured(self, env: Mapping[str, str]) -> bool:
        """True when this channel's env vars are set."""
        return all(env.get(k) for k in _REQUIRED)

    def send(self, payload: AlertPayload, env: Mapping[str, str]) -> None:
        """Deliver the payload."""
        msg = build_message(payload, env)
        host = env["SCRAPEWATCH_SMTP_HOST"]
        try:
            port = int(env.get("SCRAPEWATCH_SMTP_PORT") or 587)
        except ValueError:
            raise AlertError("email: SCRAPEWATCH_SMTP_PORT is not a number") from None
        starttls = (env.get("SCRAPEWATCH_SMTP_STARTTLS") or "true").strip().lower() not in (
            "0",
            "false",
            "no",
        )
        try:
            with smtplib.SMTP(host, port, timeout=TIMEOUT_SECONDS) as smtp:
                if starttls:
                    smtp.starttls()
                user = env.get("SCRAPEWATCH_SMTP_USER")
                if user:
                    smtp.login(user, env.get("SCRAPEWATCH_SMTP_PASSWORD", ""))
                smtp.send_message(msg)
        except (smtplib.SMTPException, OSError) as exc:
            raise AlertError(f"email: {type(exc).__name__}: {exc}") from None
