"""slack alert channel (stdlib HTTP/SMTP only)."""

from __future__ import annotations

from collections.abc import Mapping

from scrapewatch.alerts.base import AlertPayload


class SlackAlert:
    """See docs/architecture.md "Alerts" for the env vars and message format."""

    name = "slack"

    def configured(self, env: Mapping[str, str]) -> bool:
        """True when this channel's env vars are set."""
        raise NotImplementedError("build-progress item 15")

    def send(self, payload: AlertPayload, env: Mapping[str, str]) -> None:
        """Deliver the payload."""
        raise NotImplementedError("build-progress item 15")
