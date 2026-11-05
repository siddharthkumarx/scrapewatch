"""Guard rules file loader. Format and versioning: docs/architecture.md "Guard rules YAML"."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from scrapewatch.models import GuardLabel

BUNDLED_RULES = Path(__file__).parent / "rules" / "guard-rules.yml"


class Signal(BaseModel):
    """One condition. Exactly one of the fields is set (validated by the loader)."""

    model_config = ConfigDict(extra="forbid")
    status: list[int] | None = None
    header: dict[str, str] | None = (
        None  # {"name": ..., "regex": ...} or {"name": ..., "exists": "true"}
    )
    cookie: str | None = None  # regex on cookie names
    body_marker: str | None = None  # case-insensitive substring in the body window
    body_regex: str | None = None
    title_regex: str | None = None
    final_url_regex: str | None = None
    content_type: str | None = None  # prefix match
    size_below: int | None = None  # body_bytes < n
    size_below_baseline_pct: int | None = None  # body_bytes < pct% of baseline p50
    text_density_below: float | None = None
    text_length_below: int | None = None
    has_form_password: bool | None = None
    has_captcha_widget: bool | None = None


class Group(BaseModel):
    """All signals in ``all`` must match."""

    model_config = ConfigDict(extra="forbid")
    all: list[Signal] = Field(min_length=1)


class GuardRule(BaseModel):
    """One rule; matches when any group matches."""

    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z0-9_]+(\.[a-z0-9_]+)+$")
    vendor: str | None = None
    label: GuardLabel
    confidence: float = Field(ge=0.0, le=1.0)
    any: list[Group] = Field(min_length=1)
    notes: str | None = None
    since: str | None = None  # rules_version that introduced the rule


class GuardRuleSet(BaseModel):
    """The whole file."""

    model_config = ConfigDict(extra="forbid")
    schema_version: int
    rules_version: str  # calendar version, e.g. "2026.11.0"
    rules: list[GuardRule]

    def by_id(self) -> dict[str, GuardRule]:
        """Index rules by id."""
        return {r.id: r for r in self.rules}


def load_rules(path: Path | None = None, *, extra: list[Path] | None = None) -> GuardRuleSet:
    """Load the bundled file (or ``path``) and merge ``extra`` files; later ids override earlier."""
    raise NotImplementedError("build-progress item 4")


def validate_rules_file(path: Path) -> list[str]:
    """Return a list of problems (empty when valid): schema, duplicate ids, bad regexes."""
    raise NotImplementedError("build-progress item 4")


def rule_fixture_paths(rules: GuardRuleSet, fixtures_dir: Path) -> dict[str, list[Path]]:
    """Map rule id -> fixture files; used by the test that fails when a rule has no fixture."""
    raise NotImplementedError("build-progress item 4")


def _unused(_: Any) -> None:  # pragma: no cover
    pass
