"""Configuration: the ``scrapewatch.yml`` file, environment variables and defaults.

Resolution order (highest wins): explicit arguments > environment variables > config file >
defaults. See docs/architecture.md "Configuration".
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from scrapewatch import _yaml
from scrapewatch.errors import ConfigError

ENV_PREFIX = "SCRAPEWATCH_"
DEFAULT_CONFIG_FILE = "scrapewatch.yml"
# SCRAPEWATCH_HOME relocates the history directory (lower priority than HISTORY__PATH).
HOME_ENV = "SCRAPEWATCH_HOME"


class ProxyCost(BaseModel):
    """Cost of one proxy pool."""

    model_config = ConfigDict(extra="forbid")
    per_gb: float = 0.0


class ApiCost(BaseModel):
    """Cost of a paid request API (unblocker, SERP API, ...)."""

    model_config = ConfigDict(extra="forbid")
    per_1000_requests: float = 0.0


class BrowserCost(BaseModel):
    """Cost of browser time (hosted browsers bill by the minute)."""

    model_config = ConfigDict(extra="forbid")
    per_minute: float = 0.0


class CostConfig(BaseModel):
    """The cost model. Formulas: docs/architecture.md "Cost model"."""

    model_config = ConfigDict(extra="forbid")
    currency: str = "USD"
    default_proxy: str = "direct"
    proxies: dict[str, ProxyCost] = Field(default_factory=lambda: {"direct": ProxyCost()})
    apis: dict[str, ApiCost] = Field(default_factory=dict)
    browser: BrowserCost = Field(default_factory=BrowserCost)


class HistoryConfig(BaseModel):
    """Where baselines live."""

    model_config = ConfigDict(extra="forbid")
    path: Path = Path(".scrapewatch")
    backend: str = "sqlite"  # "sqlite" (default) or "jsonl"
    keep_runs: int = 50
    keep_event_days: int = 30
    baseline_runs: int = 7


class AlertConfig(BaseModel):
    """Alert routing; credentials come from environment variables only."""

    model_config = ConfigDict(extra="forbid")
    on: list[str] = Field(default_factory=lambda: ["hold"])  # any of pass, warn, hold
    channels: list[str] = Field(default_factory=list)  # telegram, slack, email, webhook


class GuardConfig(BaseModel):
    """Response guard settings."""

    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    rules_path: Path | None = None  # None = bundled rules file
    extra_rules: list[Path] = Field(default_factory=list)
    body_window_bytes: int = 65536


class Settings(BaseModel):
    """Top-level settings object."""

    model_config = ConfigDict(extra="forbid")
    name: str = "default"
    rules: Path | None = None  # data rules file
    cost: CostConfig = Field(default_factory=CostConfig)
    history: HistoryConfig = Field(default_factory=HistoryConfig)
    alerts: AlertConfig = Field(default_factory=AlertConfig)
    guard: GuardConfig = Field(default_factory=GuardConfig)
    report_dir: Path = Path(".scrapewatch/reports")
    store_events: bool = True


def load_settings(
    path: Path | str | None = None,
    *,
    env: Mapping[str, str] | None = None,
    overrides: Mapping[str, Any] | None = None,
) -> Settings:
    """Load settings from a YAML file, the environment and explicit overrides.

    Relative ``rules``, ``guard.rules_path`` and ``guard.extra_rules`` paths in the file are
    resolved against the file's directory; ``history.path`` and ``report_dir`` stay relative
    to the working directory.

    Args:
        path: ``scrapewatch.yml``; when None, looks in the current directory, then uses defaults.
        env: environment mapping (defaults to ``os.environ``). Keys use ``SCRAPEWATCH_`` prefix
            and double underscores for nesting, e.g. ``SCRAPEWATCH_HISTORY__PATH``.
        overrides: explicit values that win over everything (nested dicts or dotted keys).

    Returns:
        A validated Settings object.

    Raises:
        ConfigError: when the file or a value is invalid.
    """
    data: dict[str, Any] = {}
    if path is None and Path(DEFAULT_CONFIG_FILE).is_file():
        path = DEFAULT_CONFIG_FILE
    if path is not None:
        data = _read_file(Path(path))
    _deep_merge(data, _from_env(os.environ if env is None else env))
    if overrides:
        _deep_merge(data, _expand_dotted(overrides))
    try:
        return Settings.model_validate(data)
    except ValidationError as exc:
        where = f" in {path}" if path is not None else ""
        raise ConfigError(f"invalid settings{where}: {_short_errors(exc)}") from exc


def config_hash(settings: Settings) -> str:
    """Return a stable short hash of the settings (stored on the Run for comparability).

    First 6 hex characters of sha256 over the canonical JSON of the settings without paths,
    so moving a project does not change the hash but changing a price or threshold does.
    """
    data = settings.model_dump(mode="json", exclude=_PATH_FIELDS)
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:6]


_PATH_FIELDS: dict[str, Any] = {
    "rules": True,
    "report_dir": True,
    "history": {"path"},
    "guard": {"rules_path", "extra_rules"},
}


def _read_file(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read config file {path}: {exc}") from exc
    try:
        raw = _yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"config file {path} is not valid YAML: {exc}") from exc
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError(f"config file {path} must contain a mapping at the top level")
    base = path.resolve().parent
    if isinstance(raw.get("rules"), str):
        raw["rules"] = str(_relative_to(base, raw["rules"]))
    guard = raw.get("guard")
    if isinstance(guard, dict):
        if isinstance(guard.get("rules_path"), str):
            guard["rules_path"] = str(_relative_to(base, guard["rules_path"]))
        if isinstance(guard.get("extra_rules"), list):
            guard["extra_rules"] = [
                str(_relative_to(base, p)) if isinstance(p, str) else p
                for p in guard["extra_rules"]
            ]
    return raw


def _relative_to(base: Path, value: str) -> Path:
    p = Path(value).expanduser()
    return p if p.is_absolute() else base / p


def _from_env(env: Mapping[str, str]) -> dict[str, Any]:
    """Collect ``SCRAPEWATCH_<FIELD>[__<SUBFIELD>...]`` variables for known settings fields.

    Variables whose first segment is not a settings field (alert credentials, for example)
    are ignored here. Values starting with ``[`` or ``{`` are parsed as YAML flow collections.
    """
    out: dict[str, Any] = {}
    top = set(Settings.model_fields)
    home = env.get(HOME_ENV)
    if home:
        out["history"] = {"path": home}
    for key, value in env.items():
        if not key.startswith(ENV_PREFIX) or key == HOME_ENV:
            continue
        parts = [p.lower() for p in key[len(ENV_PREFIX) :].split("__")]
        if not parts or parts[0] not in top or any(not p for p in parts):
            continue
        _deep_merge(out, _nest(parts, _env_value(value)))
    return out


def _env_value(value: str) -> Any:
    stripped = value.strip()
    if stripped.startswith(("[", "{")):
        try:
            return _yaml.safe_load(stripped)
        except yaml.YAMLError as exc:
            raise ConfigError(f"cannot parse environment value {value!r}: {exc}") from exc
    return value


def _nest(parts: list[str], value: Any) -> dict[str, Any]:
    node: Any = value
    for part in reversed(parts):
        node = {part: node}
    out: dict[str, Any] = node
    return out


def _expand_dotted(values: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in values.items():
        _deep_merge(out, _nest(key.split("."), value))
    return out


def _deep_merge(target: dict[str, Any], source: Mapping[str, Any]) -> dict[str, Any]:
    for key, value in source.items():
        current = target.get(key)
        if isinstance(current, dict) and isinstance(value, Mapping):
            _deep_merge(current, value)
        elif isinstance(value, Mapping):
            target[key] = _deep_merge({}, value)
        else:
            target[key] = value
    return target


def _short_errors(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors()[:5]:
        loc = ".".join(str(x) for x in err["loc"])
        parts.append(f"{loc}: {err['msg']}")
    return "; ".join(parts)
