import pathlib
import weakref
from collections.abc import Iterator

import pytest

from scrapewatch.history.store import HistoryStore

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> pathlib.Path:
    return FIXTURES


@pytest.fixture(autouse=True)
def _isolated_cwd(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test runs in its own directory, so default history paths never touch the repo."""
    monkeypatch.chdir(tmp_path)
    for key in list(__import__("os").environ):
        if key.startswith("SCRAPEWATCH_"):
            monkeypatch.delenv(key)


@pytest.fixture(autouse=True)
def _close_histories(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Close every history store a test opened and left open (a Watch never finished).

    Weak references, so a test can still check that a dropped store is garbage-collected.
    """
    opened: weakref.WeakSet[HistoryStore] = weakref.WeakSet()
    real_open = HistoryStore.open

    def tracking_open(self: HistoryStore) -> None:
        real_open(self)
        opened.add(self)

    monkeypatch.setattr(HistoryStore, "open", tracking_open)
    yield
    for store in list(opened):
        store.close()
