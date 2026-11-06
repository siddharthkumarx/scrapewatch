"""Guard rules file loader. Format and versioning: docs/architecture.md "Guard rules YAML"."""

from __future__ import annotations

import functools
import re
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, ValidationError

from scrapewatch import _yaml
from scrapewatch.errors import RulesError
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

    _patterns: dict[str, re.Pattern[str]] = PrivateAttr(default_factory=dict)
    _markers: list[str] | None = PrivateAttr(default=None)

    def by_id(self) -> dict[str, GuardRule]:
        """Index rules by id."""
        return {r.id: r for r in self.rules}

    def pattern(self, regex: str) -> re.Pattern[str]:
        """The compiled (case-insensitive) form of a regex used by a signal."""
        compiled = self._patterns.get(regex)
        if compiled is None:
            compiled = re.compile(regex, re.IGNORECASE)
            self._patterns[regex] = compiled
        return compiled

    def markers(self) -> list[str]:
        """Every ``body_marker`` string in the rules, lower-cased and sorted (cached)."""
        if self._markers is not None:
            return self._markers
        found = {
            s.body_marker.lower() for r in self.rules for g in r.any for s in g.all if s.body_marker
        }
        self._markers = sorted(found)
        return self._markers


# A group that contains a quantifier and is itself quantified, e.g. (.*)+ or (a+)*: the
# classic catastrophic-backtracking shape. Rules must not use it.
_NESTED_QUANTIFIER_RE = re.compile(r"\((?:[^()\\]|\\.)*[*+}](?:[^()\\]|\\.)*\)[*+{]")
_ADJACENT_WILDCARDS_RE = re.compile(r"\.[*+]\??\.[*+]")
MAX_REGEX_LENGTH = 300
REGEX_FIELDS = ("cookie", "body_regex", "title_regex", "final_url_regex")


def check_regex(regex: str) -> str | None:
    """Return a problem description when a rule regex is invalid or unbounded, else None."""
    if len(regex) > MAX_REGEX_LENGTH:
        return f"regex longer than {MAX_REGEX_LENGTH} characters: {regex[:40]!r}..."
    try:
        re.compile(regex)
    except re.error as exc:
        return f"regex {regex!r} does not compile: {exc}"
    if _NESTED_QUANTIFIER_RE.search(regex) or _ADJACENT_WILDCARDS_RE.search(regex):
        return f"regex {regex!r} has nested or adjacent unbounded quantifiers"
    return None


def _signal_problems(rule_id: str, signal: Signal) -> list[str]:
    set_fields = [k for k, v in signal.model_dump().items() if v is not None]
    if len(set_fields) != 1:
        return [f"{rule_id}: a signal must have exactly one key, got {set_fields or 'none'}"]
    problems: list[str] = []
    if signal.header is not None:
        keys = set(signal.header)
        if "name" not in keys or len(keys - {"name"}) != 1 or not keys & {"regex", "exists"}:
            problems.append(f"{rule_id}: header signal needs name and one of regex/exists")
        elif "exists" in keys and str(signal.header["exists"]).lower() != "true":
            problems.append(f'{rule_id}: header exists must be "true"')
        elif "regex" in keys and (msg := check_regex(signal.header["regex"])):
            problems.append(f"{rule_id}: {msg}")
    for field in REGEX_FIELDS:
        value = getattr(signal, field)
        if value is not None and (msg := check_regex(value)):
            problems.append(f"{rule_id}: {msg}")
    if signal.body_marker is not None and (
        not signal.body_marker.strip() or len(signal.body_marker) > 200
    ):
        problems.append(f"{rule_id}: body_marker must be 1 to 200 characters")
    return problems


def _parse_file(path: Path) -> tuple[GuardRuleSet | None, list[str]]:
    try:
        raw = _yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        return None, [f"cannot read {path}: {exc}"]
    except yaml.YAMLError as exc:
        return None, [f"{path} is not valid YAML: {exc}"]
    if not isinstance(raw, dict):
        return None, [f"{path} must contain a mapping"]
    try:
        ruleset = GuardRuleSet.model_validate(raw)
    except ValidationError as exc:
        errs = [f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']}" for e in exc.errors()[:10]]
        return None, [f"{path}: {e}" for e in errs]
    problems: list[str] = []
    if ruleset.schema_version != 1:
        problems.append(f"{path}: unsupported schema_version {ruleset.schema_version}")
    seen: set[str] = set()
    for rule in ruleset.rules:
        if rule.id in seen:
            problems.append(f"{path}: duplicate rule id {rule.id}")
        seen.add(rule.id)
        for group in rule.any:
            for signal in group.all:
                problems.extend(_signal_problems(rule.id, signal))
    return ruleset, problems


def validate_rules_file(path: Path) -> list[str]:
    """Return a list of problems (empty when valid): schema, duplicate ids, bad regexes."""
    _, problems = _parse_file(Path(path))
    return problems


def load_rules(path: Path | None = None, *, extra: list[Path] | None = None) -> GuardRuleSet:
    """Load the bundled file (or ``path``) and merge ``extra`` files; later ids override earlier.

    Raises:
        RulesError: when a file is unreadable or invalid (the problems are listed).
    """
    base, problems = _parse_file(Path(path) if path else BUNDLED_RULES)
    if base is None or problems:
        raise RulesError("; ".join(problems))
    merged = {r.id: r for r in base.rules}
    for extra_path in extra or []:
        more, problems = _parse_file(Path(extra_path))
        if more is None or problems:
            raise RulesError("; ".join(problems))
        for rule in more.rules:
            merged[rule.id] = rule
    return GuardRuleSet(
        schema_version=base.schema_version,
        rules_version=base.rules_version,
        rules=list(merged.values()),
    )


@functools.lru_cache(maxsize=1)
def bundled_rules() -> GuardRuleSet:
    """The bundled rule set, loaded once per process."""
    return load_rules()


def bundled_markers() -> list[str]:
    """Markers of the bundled rule set (what ``extract_features`` looks for by default)."""
    return bundled_rules().markers()


def rule_fixture_paths(rules: GuardRuleSet, fixtures_dir: Path) -> dict[str, list[Path]]:
    """Map rule id -> fixture files; used by the test that fails when a rule has no fixture.

    A fixture belongs to the rule named in its ``rule:`` key; files without one are matched
    by name (``<rule id>.yml`` or ``<rule id>.<variant>.yml``).
    """
    out: dict[str, list[Path]] = {r.id: [] for r in rules.rules}
    for path in sorted(Path(fixtures_dir).glob("*.yml")):
        try:
            data = _yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            continue
        rule_id = data.get("rule") if isinstance(data, dict) else None
        if rule_id is None:
            stem = path.name[: -len(".yml")]
            rule_id = next((rid for rid in out if stem == rid or stem.startswith(rid + ".")), None)
        if rule_id in out:
            out[rule_id].append(path)
    return out
