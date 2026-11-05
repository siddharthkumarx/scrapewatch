"""Feature extraction: the only thing the guard (and fixtures) ever see of a response."""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field

MARKER_SNIPPET_MAX = 200  # characters; fixtures may never hold more of a page than this


class Features(BaseModel):
    """Features of one response. This is also the fixture format (tests/guard/fixtures)."""

    model_config = ConfigDict(extra="forbid")
    status: int | None = None
    headers: dict[str, str] = Field(default_factory=dict)  # lower-case names, redacted
    cookie_names: list[str] = Field(default_factory=list)
    final_url: str | None = None
    request_url: str | None = None
    content_type: str | None = None
    title: str | None = None
    body_bytes: int = 0
    text_length: int = 0
    text_density: float = 0.0  # visible text chars / body bytes, over the body window
    markers: list[str] = Field(default_factory=list)  # short lower-case snippets found in body
    has_form_password: bool = False
    has_captcha_widget: bool = False
    baseline_p50_bytes: int | None = None  # from history, for empty-shell detection
    body_window: str | None = None  # in-memory only; never written to fixtures or history


def extract_features(
    *,
    status: int | None,
    headers: Mapping[str, str],
    body: bytes | str | None,
    request_url: str | None,
    final_url: str | None,
    window_bytes: int = 65536,
    baseline_p50_bytes: int | None = None,
) -> Features:
    """Compute features from a raw response. Only the first ``window_bytes`` of body are read."""
    raise NotImplementedError("build-progress item 4")


def markers_for_fixture(features: Features, *, max_len: int = MARKER_SNIPPET_MAX) -> Features:
    """Drop ``body_window`` and clip markers so a Features object is safe to commit as a fixture."""
    raise NotImplementedError("build-progress item 4")
