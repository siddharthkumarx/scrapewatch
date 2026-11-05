"""Normalizer protocol and the pipeline that applies a ``clean:`` list to a field."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from scrapewatch.models import Finding, Fix


class CleanOutcome(BaseModel):
    """What one normalizer returns."""

    model_config = ConfigDict(extra="forbid")
    value: Any = None
    changed: bool = False
    flag: str | None = None  # finding check id suffix, e.g. "ambiguous_separator"
    flag_message: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)  # e.g. {"currency": "EUR"} for price


Normalizer = Callable[[Any, Mapping[str, Any]], CleanOutcome]

NORMALIZERS: dict[str, Normalizer] = {}  # filled by registering modules on import


def register(name: str) -> Callable[[Normalizer], Normalizer]:
    """Decorator registering a normalizer under ``name``."""

    def deco(fn: Normalizer) -> Normalizer:
        NORMALIZERS[name] = fn
        return fn

    return deco


def apply_normalizers(
    field: str,
    value: Any,
    names: list[str],
    options: Mapping[str, Any],
    *,
    run_id: str,
    record_id: str,
) -> tuple[Any, list[Fix], list[Finding], dict[str, Any]]:
    """Apply normalizers in order, stopping at the first flag.

    Returns (value, fixes, findings, extra).
    """
    raise NotImplementedError("build-progress item 10")
