"""webhook alert channel (stdlib HTTP only): the AlertPayload as JSON, optionally signed."""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping

from scrapewatch.alerts.base import AlertPayload, dumps, post_json

SIGNATURE_HEADER = "X-Scrapewatch-Signature"


def signature(body: bytes, secret: str) -> str:
    """``sha256=<hex HMAC of the body>``; receivers recompute it with the shared secret."""
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


class WebhookAlert:
    """``SCRAPEWATCH_WEBHOOK_URL`` and optional ``SCRAPEWATCH_WEBHOOK_SECRET``."""

    name = "webhook"

    def configured(self, env: Mapping[str, str]) -> bool:
        """True when this channel's env vars are set."""
        return bool(env.get("SCRAPEWATCH_WEBHOOK_URL"))

    def send(self, payload: AlertPayload, env: Mapping[str, str]) -> None:
        """Deliver the payload."""
        body = dumps(payload.model_dump(mode="json"))
        headers = {"User-Agent": "scrapewatch-alerts"}
        secret = env.get("SCRAPEWATCH_WEBHOOK_SECRET")
        if secret:
            headers[SIGNATURE_HEADER] = signature(body, secret)
        post_json(env["SCRAPEWATCH_WEBHOOK_URL"], body, headers=headers, what="webhook")
