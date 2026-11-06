"""telegram alert channel (stdlib HTTP only): Bot API ``sendMessage`` in HTML parse mode."""

from __future__ import annotations

from collections.abc import Mapping
from html import escape

from scrapewatch.alerts.base import AlertPayload, dumps, message_lines, post_json

API = "https://api.telegram.org"


def request_body(payload: AlertPayload, chat_id: str) -> bytes:
    """The JSON body sent to ``sendMessage`` (golden: tests/fixtures/alerts/telegram.json)."""
    lines = [escape(line, quote=False) for line in message_lines(payload)]
    lines[0] = f"<b>{lines[0]}</b>"
    return dumps(
        {
            "chat_id": chat_id,
            "text": "\n".join(lines),
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
    )


class TelegramAlert:
    """``SCRAPEWATCH_TELEGRAM_BOT_TOKEN`` and ``SCRAPEWATCH_TELEGRAM_CHAT_ID``."""

    name = "telegram"

    def configured(self, env: Mapping[str, str]) -> bool:
        """True when this channel's env vars are set."""
        return bool(env.get("SCRAPEWATCH_TELEGRAM_BOT_TOKEN")) and bool(
            env.get("SCRAPEWATCH_TELEGRAM_CHAT_ID")
        )

    def send(self, payload: AlertPayload, env: Mapping[str, str]) -> None:
        """Deliver the payload (the token is in the URL, so errors never show the URL)."""
        token = env["SCRAPEWATCH_TELEGRAM_BOT_TOKEN"]
        body = request_body(payload, env["SCRAPEWATCH_TELEGRAM_CHAT_ID"])
        post_json(f"{API}/bot{token}/sendMessage", body, what="telegram sendMessage")
