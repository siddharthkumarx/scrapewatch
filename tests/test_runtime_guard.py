"""Item 6: size baselines from history feed empty-shell detection; redirects; retries."""

from pathlib import Path

import pytest

from scrapewatch.config import Settings
from scrapewatch.history.store import HistoryStore
from scrapewatch.models import GuardLabel, VerdictStatus
from scrapewatch.runtime import Watch, watch

HTML = {"content-type": "text/html; charset=utf-8"}


def _page(kib: int) -> bytes:
    para = b"<p>" + b"A quiet garden and other stories about the sea. " * 20 + b"</p>\n"
    body = b"<html><head><title>Catalogue</title></head><body>"
    while len(body) < kib * 1024:
        body += para
    return body + b"</body></html>"


def _shell(kib: int) -> bytes:
    body = b"<html><head><title>Catalogue</title><script>"
    body += b"var x = 1;" * (kib * 1024 // 10)
    return body + b"</script></head><body><div id='app'></div></body></html>"


def _settings(tmp_path: Path) -> Settings:
    return Settings(name="books", history={"path": tmp_path})


def test_second_run_detects_small_pages_as_empty(tmp_path: Path) -> None:
    with watch(settings=_settings(tmp_path)) as w1:
        for i in range(1, 6):
            ev = w1.record_response(
                f"https://books.toscrape.com/catalogue/page-{i}.html",
                status=200,
                headers=HTML,
                body=_page(50),
            )
            assert ev is not None
            assert ev.guard.label == GuardLabel.OK
    assert w1.verdict.status == VerdictStatus.PASS
    with HistoryStore(tmp_path) as store:
        base = store.size_baseline("books.toscrape.com", "/catalogue/page-{n}.html")
    assert base is not None
    assert base[1] == 5
    with watch(settings=_settings(tmp_path)) as w2:
        small = w2.record_response(
            "https://books.toscrape.com/catalogue/page-7.html",
            status=200,
            headers=HTML,
            body=_shell(10),  # about 20% of the usual size, almost no text
        )
        normal = w2.record_response(
            "https://books.toscrape.com/catalogue/page-8.html",
            status=200,
            headers=HTML,
            body=_page(48),
        )
    assert small is not None
    assert normal is not None
    assert small.guard.label == GuardLabel.EMPTY
    assert small.guard.rule_id == "generic.empty.small_vs_baseline"
    assert any("of baseline" in s for s in small.guard.signals)
    assert normal.guard.label == GuardLabel.OK


def test_baselines_not_updated_by_a_held_run(tmp_path: Path) -> None:
    def scraper() -> None:
        with watch(settings=_settings(tmp_path)) as w:
            w.record_response(
                "https://books.toscrape.com/catalogue/page-1.html",
                status=200,
                headers=HTML,
                body=_page(50),
            )
            raise RuntimeError("crash")

    with pytest.raises(RuntimeError, match="crash"):
        scraper()
    with HistoryStore(tmp_path) as store:
        assert store.size_baseline("books.toscrape.com", "/catalogue/page-{n}.html") is None


def test_size_baseline_lookup_is_cached(tmp_path: Path) -> None:
    w = Watch(_settings(tmp_path))
    w.start()
    calls: list[tuple[str, str]] = []
    assert w.store is not None
    original = w.store.size_baseline

    def spy(domain: str, pattern: str) -> tuple[int, int] | None:
        calls.append((domain, pattern))
        return original(domain, pattern)

    w.store.size_baseline = spy  # type: ignore[method-assign,assignment]
    for i in range(5):
        w.record_response(f"https://a.example/p/{i}", status=200, headers=HTML, body=_page(5))
    assert calls == [("a.example", "/p/{n}")]
    w.finish()
    assert Watch(Settings()).size_baseline("a.example", "https://a.example/") is None


def test_redirect_to_login(tmp_path: Path) -> None:
    with watch(settings=_settings(tmp_path)) as w:
        ev = w.record_response(
            "https://shop.example/p/1",
            status=200,
            headers=HTML,
            body=_page(20),
            final_url="https://shop.example/login?next=%2Fp%2F1",
        )
        same = w.record_response(
            "https://shop.example/login",
            status=200,
            headers=HTML,
            body=_page(20),
            final_url="https://shop.example/login",
        )
    assert ev is not None
    assert same is not None
    assert ev.guard.label == GuardLabel.REDIRECT
    assert ev.guard.rule_id == "generic.redirect.login"
    assert ev.final_url == "https://shop.example/login?next=%2Fp%2F1"
    assert same.guard.label == GuardLabel.OK  # final_url == url: not a redirect
    assert same.final_url is None


def test_retry_chain_from_request_key(tmp_path: Path) -> None:
    with watch(settings=_settings(tmp_path)) as w:
        url = "https://books.toscrape.com/catalogue/page-9.html"
        meta = {"request_key": "GET " + url}
        first = w.record_response(url, status=503, meta=meta)
        second = w.record_response(url, status=503, meta=meta)
        third = w.record_response(url, status=200, headers=HTML, body=_page(5), meta=meta)
        other = w.record_response(url + "?x", status=200, meta={"request_key": "other"})
    assert first is not None
    assert second is not None
    assert third is not None
    assert other is not None
    assert [e.attempt for e in (first, second, third)] == [1, 2, 3]
    assert first.retry_of is None
    assert second.retry_of == first.event_id
    assert third.retry_of == first.event_id
    assert other.attempt == 1
    assert w.run.counts.retries == 2
    assert w.run.counts.responses == 4


def test_retry_times_meta() -> None:
    w = Watch(Settings(history={"path": Path("h")}))
    w.start()
    ev = w.record_response("https://a.example/", status=200, meta={"retry_times": 2})
    assert ev is not None
    assert ev.attempt == 3
    w.finish()
