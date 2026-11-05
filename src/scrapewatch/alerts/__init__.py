"""Alerts: Telegram, Slack, email, webhook. Credentials from environment variables only."""

from scrapewatch.alerts.base import Alert, AlertPayload, build_payload, send_alerts

__all__ = ["Alert", "AlertPayload", "build_payload", "send_alerts"]
