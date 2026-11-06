"""slack alert channel (stdlib HTTP only): an incoming webhook with Block Kit text."""

from __future__ import annotations

from collections.abc import Mapping

from scrapewatch.alerts.base import AlertPayload, dumps, message_lines, message_text, post_json


def _mrkdwn(text: str) -> str:
    """Escape the three characters Slack treats as control characters."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def request_body(payload: AlertPayload) -> bytes:
    """The JSON body posted to the webhook (golden: tests/fixtures/alerts/slack.json)."""
    head, *rest = message_lines(payload)
    return dumps(
        {
            "text": _mrkdwn(message_text(payload)),
            "blocks": [
                {"type": "header", "text": {"type": "plain_text", "text": head}},
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": _mrkdwn("\n".join(rest))},
                },
            ],
        }
    )


class SlackAlert:
    """``SCRAPEWATCH_SLACK_WEBHOOK_URL``."""

    name = "slack"

    def configured(self, env: Mapping[str, str]) -> bool:
        """True when this channel's env vars are set."""
        return bool(env.get("SCRAPEWATCH_SLACK_WEBHOOK_URL"))

    def send(self, payload: AlertPayload, env: Mapping[str, str]) -> None:
        """Deliver the payload."""
        url = env["SCRAPEWATCH_SLACK_WEBHOOK_URL"]
        post_json(url, request_body(payload), what="slack webhook")
