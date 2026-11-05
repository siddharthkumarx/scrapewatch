"""Configuration: the ``scrapewatch.yml`` file, environment variables and defaults.

Resolution order (highest wins): explicit arguments > environment variables > config file >
defaults. See docs/architecture.md "Configuration".
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

ENV_PREFIX = "SCRAPEWATCH_"


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
    env: dict[str, str] | None = None,
    overrides: dict[str, Any] | None = None,
) -> Settings:
    """Load settings from a YAML file, the environment and explicit overrides.

    Args:
        path: ``scrapewatch.yml``; when None, looks in the current directory, then uses defaults.
        env: environment mapping (defaults to ``os.environ``). Keys use ``SCRAPEWATCH_`` prefix
            and double underscores for nesting, e.g. ``SCRAPEWATCH_HISTORY__PATH``.
        overrides: explicit values that win over everything.

    Returns:
        A validated Settings object.

    Raises:
        ConfigError: when the file or a value is invalid.
    """
    raise NotImplementedError("build-progress item 1")


def config_hash(settings: Settings) -> str:
    """Return a stable short hash of the settings (stored on the Run for comparability)."""
    raise NotImplementedError("build-progress item 1")
