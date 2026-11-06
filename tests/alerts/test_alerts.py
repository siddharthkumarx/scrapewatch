"""Alerts (build item 15): payload, four channels, dispatch, wiring, ``alert test``."""

from __future__ import annotations

import hashlib
import hmac
import http.server
import json
import os
import threading
from collections.abc import Iterator
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any, ClassVar

import pytest
from click.testing import CliRunner

from scrapewatch.alerts import base, send_alerts
from scrapewatch.alerts.base import AlertPayload, build_payload, message_text
from scrapewatch.alerts.email import EmailAlert, build_message
from scrapewatch.alerts.slack import request_body as slack_body
from scrapewatch.alerts.telegram import request_body as telegram_body
from scrapewatch.alerts.webhook import SIGNATURE_HEADER, signature
from scrapewatch.cli.main import main
from scrapewatch.config import Settings
from scrapewatch.models import CostBreakdown, Run, RunCounts, Verdict, VerdictStatus, per_1000
from scrapewatch.runtime import watch

ALERTS = Path(__file__).resolve().parents[1] / "fixtures" / "alerts"
REGEN = os.environ.get("SCRAPEWATCH_REGEN_GOLDEN") == "1"


def _run(**verdict: Any) -> Run:
    t0 = datetime(2026, 11, 5, 14, 12, tzinfo=timezone.utc)
    per_usable = 0.41 / 390
    return Run(
        run_id="9a1b2c3d4e5f60718293a4b5c6d7e8f9",
        name="books",
        started_at=t0,
        finished_at=t0.replace(minute=14, second=31),
        counts=RunCounts(
            records=412, usable_records=390, document_responses=1000, bad_documents=234
        ),
        cost=CostBreakdown(
            total=0.41,
            usable_records=390,
            per_usable_record=per_usable,
            per_1000_usable_records=per_1000(per_usable),
        ),
        verdict=Verdict(
            status=VerdictStatus(verdict.get("status", "hold")),
            reasons=[
                "Blocked share 23.4% is above 20%.",
                "Row count 412 is 58% below baseline 980.",
            ],
        ),
        report_path=".scrapewatch/reports/books/2026-11-05T14-12-00_9a1b2c3d.html",
    )


class _Capture:
    """A fake urlopen that records requests."""

    def __init__(self, fail: Exception | None = None) -> None:
        self.requests: list[Any] = []
        self.fail = fail

    def __call__(self, request: Any, timeout: float) -> Any:
        assert timeout == 10
        self.requests.append(request)
        if self.fail is not None:
            raise self.fail

        class _Resp:
            def __enter__(self) -> _Resp:
                return self

            def __exit__(self, *a: object) -> None:
                return None

            def read(self) -> bytes:
                return b"{}"

        return _Resp()


@pytest.fixture
def capture(monkeypatch: pytest.MonkeyPatch) -> _Capture:
    cap = _Capture()
    monkeypatch.setattr("urllib.request.urlopen", cap)
    return cap


def _golden(name: str, body: bytes) -> None:
    path = ALERTS / name
    data = json.loads(body)
    if REGEN:
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", "utf-8")
    assert data == json.loads(path.read_text("utf-8"))


def test_payload_and_message() -> None:
    payload = build_payload(_run())
    assert payload.blocked_share_pct == 23.4
    assert payload.cost_per_1000_usable_records == pytest.approx(0.41 / 390 * 1000)
    text = message_text(payload)
    assert text.splitlines() == [
        "scrapewatch · books · HOLD",
        "Blocked share 23.4% is above 20%. Row count 412 is 58% below baseline 980.",
        "Records 412 (usable 390) · cost $0.41 · $1.05 per 1,000 usable records",
        "Report: .scrapewatch/reports/books/2026-11-05T14-12-00_9a1b2c3d.html",
    ]
    _golden("payload.json", base.dumps(payload.model_dump(mode="json")))


def test_cost_headline_switches_at_one_cent() -> None:
    run = _run()
    run.cost.per_usable_record = 0.0000115
    payload = build_payload(run)
    assert payload.cost_per_1000_usable_records == pytest.approx(0.0115)
    assert "$0.0115 per 1,000 usable records" in message_text(payload)
    run.cost.per_usable_record = 0.25
    assert "$0.25 per usable record" in message_text(build_payload(run))
    run.cost.per_usable_record = None
    assert "no usable records" in message_text(build_payload(run))


def test_slack_and_telegram_bodies_match_goldens(capture: _Capture) -> None:
    payload = build_payload(_run())
    env = {
        "SCRAPEWATCH_SLACK_WEBHOOK_URL": "https://hooks.slack.example/T/B/x",
        "SCRAPEWATCH_TELEGRAM_BOT_TOKEN": "123:secret-token",
        "SCRAPEWATCH_TELEGRAM_CHAT_ID": "-1001",
    }
    _golden("slack.json", slack_body(payload))
    _golden("telegram.json", telegram_body(payload, "-1001"))
    results = base.deliver(payload, channels_wanted=None, env=env)
    assert results == {"telegram": "ok", "slack": "ok"}
    urls = [r.full_url for r in capture.requests]
    assert urls == [
        "https://api.telegram.org/bot123:secret-token/sendMessage",
        "https://hooks.slack.example/T/B/x",
    ]
    assert capture.requests[1].data == slack_body(payload)
    assert capture.requests[0].get_header("Content-type") == "application/json"


def test_errors_never_show_the_token(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.error
    from email.message import Message

    err = urllib.error.HTTPError(
        "https://api.telegram.org/bot123:secret/x", 401, "no", Message(), None
    )
    monkeypatch.setattr("urllib.request.urlopen", _Capture(fail=err))
    env = {"SCRAPEWATCH_TELEGRAM_BOT_TOKEN": "123:secret", "SCRAPEWATCH_TELEGRAM_CHAT_ID": "1"}
    results = base.deliver(build_payload(_run()), channels_wanted=None, env=env)
    assert results == {"telegram": "telegram sendMessage: HTTP 401"}
    monkeypatch.setattr("urllib.request.urlopen", _Capture(fail=OSError("refused")))
    out = base.deliver(build_payload(_run()), channels_wanted=None, env=env)
    assert "secret" not in out["telegram"]
    assert "refused" in out["telegram"]


class _Hook(http.server.BaseHTTPRequestHandler):
    received: ClassVar[list[tuple[dict[str, str], bytes]]] = []

    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers["Content-Length"]))
        _Hook.received.append((dict(self.headers), body))
        self.send_response(204)
        self.end_headers()

    def log_message(self, *args: object) -> None:
        return None


@pytest.fixture
def server() -> Iterator[str]:
    _Hook.received = []
    httpd = http.server.HTTPServer(("127.0.0.1", 0), _Hook)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}/hook"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_webhook_to_a_local_server_with_signature(server: str) -> None:
    env = {"SCRAPEWATCH_WEBHOOK_URL": server, "SCRAPEWATCH_WEBHOOK_SECRET": "s3"}
    assert send_alerts(_run(), on=["hold"], channels=None, env=env) == {"webhook": "ok"}
    headers, body = _Hook.received[0]
    payload = AlertPayload.model_validate_json(body)
    assert payload.verdict == "hold"
    assert payload.records == 412
    expected = "sha256=" + hmac.new(b"s3", body, hashlib.sha256).hexdigest()
    assert headers[SIGNATURE_HEADER] == expected == signature(body, "s3")
    env.pop("SCRAPEWATCH_WEBHOOK_SECRET")
    send_alerts(_run(), on=["hold"], channels=["webhook"], env=env)
    assert SIGNATURE_HEADER not in _Hook.received[1][0]


class _FakeSMTP:
    sent: ClassVar[list[EmailMessage]] = []
    calls: ClassVar[list[str]] = []
    fail: ClassVar[Exception | None] = None

    def __init__(self, host: str, port: int, timeout: float) -> None:
        _FakeSMTP.calls.append(f"connect {host}:{port} {timeout}")

    def __enter__(self) -> _FakeSMTP:
        return self

    def __exit__(self, *a: object) -> None:
        return None

    def starttls(self) -> None:
        _FakeSMTP.calls.append("starttls")

    def login(self, user: str, password: str) -> None:
        _FakeSMTP.calls.append(f"login {user}")

    def send_message(self, msg: EmailMessage) -> None:
        if _FakeSMTP.fail is not None:
            raise _FakeSMTP.fail
        _FakeSMTP.sent.append(msg)


@pytest.fixture
def smtp(monkeypatch: pytest.MonkeyPatch) -> type[_FakeSMTP]:
    _FakeSMTP.sent, _FakeSMTP.calls, _FakeSMTP.fail = [], [], None
    monkeypatch.setattr("smtplib.SMTP", _FakeSMTP)
    return _FakeSMTP


EMAIL_ENV = {
    "SCRAPEWATCH_SMTP_HOST": "smtp.example.org",
    "SCRAPEWATCH_SMTP_USER": "bot",
    "SCRAPEWATCH_SMTP_PASSWORD": "pw",
    "SCRAPEWATCH_EMAIL_FROM": "bot@example.org",
    "SCRAPEWATCH_EMAIL_TO": "a@example.org, b@example.org",
}


def test_email_attaches_small_reports(tmp_path: Path, smtp: type[_FakeSMTP]) -> None:
    report = tmp_path / "r.html"
    report.write_text("<html>report</html>", encoding="utf-8")
    run = _run()
    run.report_path = str(report)
    assert send_alerts(run, on=["hold"], channels=["email"], env=EMAIL_ENV) == {"email": "ok"}
    assert smtp.calls == ["connect smtp.example.org:587 10", "starttls", "login bot"]
    msg = smtp.sent[0]
    assert msg["Subject"] == "scrapewatch · books · HOLD"
    assert msg["To"] == "a@example.org, b@example.org"
    attachments = list(msg.iter_attachments())
    assert [a.get_filename() for a in attachments] == ["r.html"]
    report.write_bytes(b"x" * (2 * 1024 * 1024 + 1))
    assert list(build_message(build_payload(run), EMAIL_ENV).iter_attachments()) == []


def test_email_options_and_failures(smtp: type[_FakeSMTP]) -> None:
    env = {**EMAIL_ENV, "SCRAPEWATCH_SMTP_PORT": "2525", "SCRAPEWATCH_SMTP_STARTTLS": "false"}
    env.pop("SCRAPEWATCH_SMTP_USER")
    EmailAlert().send(build_payload(_run()), env)
    assert smtp.calls == ["connect smtp.example.org:2525 10"]
    smtp.fail = OSError("connection refused")
    out = send_alerts(_run(), on=["hold"], channels=["email"], env=env)
    assert out == {"email": "email: OSError: connection refused"}
    bad_port = {**env, "SCRAPEWATCH_SMTP_PORT": "x"}
    assert (
        "not a number"
        in send_alerts(_run(), on=["hold"], channels=["email"], env=bad_port)["email"]
    )


def test_on_filtering_and_env_override(capture: _Capture) -> None:
    env = {"SCRAPEWATCH_SLACK_WEBHOOK_URL": "https://hooks.slack.example/x"}
    assert send_alerts(_run(status="warn"), on=["hold"], channels=None, env=env) == {}
    assert capture.requests == []
    env["SCRAPEWATCH_ALERT_ON"] = "warn, hold"
    assert send_alerts(_run(status="warn"), on=["hold"], channels=None, env=env) == {"slack": "ok"}
    assert send_alerts(_run(status="pass"), on=["pass"], channels=None, env=env) == {}


def test_unknown_unconfigured_and_buggy_channels(monkeypatch: pytest.MonkeyPatch) -> None:
    out = send_alerts(_run(), on=["hold"], channels=["pager", "slack"], env={})
    assert out["pager"].startswith("unknown channel")
    assert out["slack"].startswith("not configured")
    assert send_alerts(_run(), on=["hold"], channels=None, env={}) == {}

    def boom(self: Any, payload: Any, env: Any) -> None:
        raise RuntimeError("bug")

    from scrapewatch.alerts.slack import SlackAlert

    monkeypatch.setattr(SlackAlert, "send", boom)
    env = {"SCRAPEWATCH_SLACK_WEBHOOK_URL": "https://x.example/"}
    assert send_alerts(_run(), on=["hold"], channels=None, env=env) == {
        "slack": "RuntimeError: bug"
    }


def test_watch_counts_failed_delivery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCRAPEWATCH_WEBHOOK_URL", "http://127.0.0.1:9/closed")
    settings = Settings(history={"path": tmp_path / "h"}, alerts={"on": ["pass"]})
    with watch("books", settings=settings) as w:
        w.record_record({"a": 1})
    assert w.run.status.value == "finished"
    assert w.run.counts.monitor_errors == 1
    from scrapewatch.history.store import open_store

    store = open_store(tmp_path / "h")
    try:
        stored = store.get_run(w.run.run_id)
        assert stored is not None
        assert stored.counts.monitor_errors == 1
    finally:
        store.close()


def test_watch_sends_on_hold(server: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCRAPEWATCH_WEBHOOK_URL", server)
    settings = Settings(history={"path": tmp_path / "h"})
    with pytest.raises(ValueError, match="scraper broke"), watch("books", settings=settings):
        raise ValueError("scraper broke")
    payload = AlertPayload.model_validate_json(_Hook.received[0][1])
    assert payload.verdict == "hold"
    assert payload.report_path is not None


def test_alert_test_command(server: str, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = CliRunner()
    none = runner.invoke(main, ["alert", "test"])
    assert none.exit_code == 1
    assert "no alert channel is configured" in none.output
    monkeypatch.setenv("SCRAPEWATCH_WEBHOOK_URL", server)
    ok = runner.invoke(main, ["alert", "test"])
    assert ok.exit_code == 0
    assert ok.output == "webhook: ok\n"
    assert AlertPayload.model_validate_json(_Hook.received[-1][1]).name == "scrapewatch-test"
    bad = runner.invoke(main, ["alert", "test", "--channel", "slack"])
    assert bad.exit_code == 1
    assert "slack: not configured" in bad.output


def test_money_formatting() -> None:
    assert base.money(None, "USD") == "-"
    assert base.money(0, "USD") == "$0"
    assert base.money(0.00001, "EUR") == "< €0.0001"
    assert base.money(0.0115, "GBP") == "£0.0115"
    assert base.money(0.005, "CHF") == "CHF 0.005"
    assert base.money(1234.5, "USD") == "$1,234.50"
