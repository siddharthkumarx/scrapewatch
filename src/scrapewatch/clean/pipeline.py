"""Normalizer protocol and the pipeline that applies a ``clean:`` list to a field."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from scrapewatch.models import Finding, FindingScope, Fix, Severity


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


@register("passthrough")
def passthrough(value: Any, options: Mapping[str, Any]) -> CleanOutcome:
    """Leave the value as it is (useful to declare a field cleaned on purpose)."""
    return CleanOutcome(value=value)


class PipelineResult(BaseModel):
    """Everything one field's ``clean:`` list produced."""

    model_config = ConfigDict(extra="forbid")
    value: Any = None
    fixes: list[Fix] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)
    extra_source: dict[str, str] = Field(default_factory=dict)  # extra key -> normalizer
    flagged: bool = False


def run_normalizers(
    field: str,
    value: Any,
    names: list[str],
    options: Mapping[str, Any],
    *,
    run_id: str,
    record_id: str,
    severity: Severity = Severity.WARN,
    field_type: str | None = None,
) -> PipelineResult:
    """Apply normalizers in order, stopping at the first flag (detailed result).

    ``None`` and ``""`` pass through without calling any normalizer. A name without an
    implementation is a passthrough. A normalizer that raises leaves the value unchanged and
    records a ``clean.<name>.error`` finding. ``field_type`` reaches the normalizers as the
    reserved option ``_field_type`` (numbers come back as int in integer fields).
    """
    out = PipelineResult(value=value)
    if field_type is not None:
        options = {**options, "_field_type": field_type}
    if value is None or (isinstance(value, str) and value == ""):
        return out  # required-ness is a field rule; normalizers never coerce None or ""
    for name in names:
        fn = NORMALIZERS.get(name, passthrough)
        try:
            outcome = fn(out.value, options)
        except NotImplementedError:  # normalizers still to be built (items 10 and 11)
            continue
        except Exception as exc:
            outcome = CleanOutcome(
                value=out.value, flag="error", flag_message=f"{type(exc).__name__}: {exc}"
            )
        if outcome.flag:
            out.flagged = True
            out.findings.append(
                Finding(
                    run_id=run_id,
                    scope=FindingScope.RECORD,
                    ref=record_id,
                    check=f"clean.{name}.{outcome.flag}",
                    severity=severity,
                    field=field,
                    message=outcome.flag_message
                    or f"{field}: {name} could not clean {out.value!r} ({outcome.flag})",
                    observed=out.value,
                )
            )
            break
        if outcome.changed:
            out.fixes.append(
                Fix(field=field, normalizer=name, before=out.value, after=outcome.value)
            )
            out.value = outcome.value
        for key, val in outcome.extra.items():
            out.extra[key] = val
            out.extra_source[key] = name
    return out


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
    r = run_normalizers(field, value, names, options, run_id=run_id, record_id=record_id)
    return r.value, r.fixes, r.findings, r.extra
