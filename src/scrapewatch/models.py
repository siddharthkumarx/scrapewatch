"""Core data model (schema_version 1).

Every object scrapewatch stores, reports or exchanges is one of these pydantic v2 models.
The JSON shapes are documented in docs/architecture.md "Core data model"; tests/test_models.py
pins them. A breaking change to any field bumps SCHEMA_VERSION and adds a migration in
``scrapewatch.history.migrations``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = 1


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _new_id() -> str:
    return uuid.uuid4().hex


class GuardLabel(str, Enum):
    """Classification of one HTTP response by the response guard."""

    OK = "ok"
    BLOCK = "block"
    CHALLENGE = "challenge"
    CAPTCHA = "captcha"
    CONSENT = "consent"
    LOGIN = "login"
    RATE_LIMIT = "rate_limit"
    EMPTY = "empty"
    REDIRECT = "redirect"
    ERROR_PAGE = "error_page"
    ERROR = "error"
    UNKNOWN = "unknown"


# Precedence when several rules match one response: first wins.
GUARD_LABEL_PRECEDENCE: tuple[GuardLabel, ...] = (
    GuardLabel.CAPTCHA,
    GuardLabel.CHALLENGE,
    GuardLabel.BLOCK,
    GuardLabel.RATE_LIMIT,
    GuardLabel.LOGIN,
    GuardLabel.CONSENT,
    GuardLabel.REDIRECT,
    GuardLabel.ERROR_PAGE,
    GuardLabel.EMPTY,
    GuardLabel.ERROR,
    GuardLabel.UNKNOWN,
    GuardLabel.OK,
)

# Labels that count as "not usable" for the blocked-share verdict rule.
BAD_LABELS: frozenset[GuardLabel] = frozenset(
    {
        GuardLabel.BLOCK,
        GuardLabel.CHALLENGE,
        GuardLabel.CAPTCHA,
        GuardLabel.CONSENT,
        GuardLabel.LOGIN,
        GuardLabel.RATE_LIMIT,
        GuardLabel.EMPTY,
        GuardLabel.REDIRECT,
        GuardLabel.ERROR_PAGE,
        GuardLabel.ERROR,
    }
)


class ResourceType(str, Enum):
    """Coarse resource type of a request, used by the cost meter and waste cutter."""

    DOCUMENT = "document"
    XHR = "xhr"
    SCRIPT = "script"
    STYLESHEET = "stylesheet"
    IMAGE = "image"
    FONT = "font"
    MEDIA = "media"
    OTHER = "other"


class BytesSource(str, Enum):
    """How wire_bytes was obtained. See docs/architecture.md "Cost model"."""

    MEASURED = "measured"  # bytes read from the socket (compressed)
    CONTENT_LENGTH = "content_length"  # Content-Length header (compressed, body only)
    DECODED = "decoded"  # len(decoded body): an over-estimate when compressed
    UNKNOWN = "unknown"


class Severity(str, Enum):
    """Severity of a finding. Maps to the verdict: hold > warn > info."""

    INFO = "info"
    WARN = "warn"
    HOLD = "hold"


class VerdictStatus(str, Enum):
    """Outcome of a run. CLI exit codes: pass=0, warn=1, hold=2 (error=3)."""

    PASS = "pass"  # noqa: S105 - not a password
    WARN = "warn"
    HOLD = "hold"


class RunStatus(str, Enum):
    """Lifecycle state of a run."""

    RUNNING = "running"
    FINISHED = "finished"
    CRASHED = "crashed"


class FindingScope(str, Enum):
    """What a finding refers to."""

    RESPONSE = "response"
    RECORD = "record"
    BATCH = "batch"
    RUN = "run"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=False, validate_assignment=True)


class GuardResult(_Model):
    """Result of classifying one response."""

    label: GuardLabel = GuardLabel.OK
    vendor: str | None = None
    rule_id: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    signals: list[str] = Field(default_factory=list)


class CacheInfo(_Model):
    """Conditional-request outcome for one response (waste cutter)."""

    conditional: bool = False  # request carried If-None-Match / If-Modified-Since
    unchanged: bool = False  # 304, or body hash equal to the stored one
    deduped: bool = False  # URL already fetched in this run; request was skipped


class ErrorInfo(_Model):
    """Transport-level error (timeout, DNS, TLS, connection reset)."""

    type: str
    message: str = ""


class ResponseEvent(_Model):
    """One request/response pair as seen by an integration.

    No body is stored. Headers are an allow-listed, redacted subset; cookies are names only.
    """

    schema_version: int = SCHEMA_VERSION
    event_id: str = Field(default_factory=_new_id)
    run_id: str
    ts: datetime = Field(default_factory=_now)
    url: str
    final_url: str | None = None
    method: str = "GET"
    domain: str
    status: int | None = None
    elapsed_ms: float | None = None
    wire_bytes: int = Field(default=0, ge=0)
    body_bytes: int = Field(default=0, ge=0)
    bytes_source: BytesSource = BytesSource.UNKNOWN
    resource_type: ResourceType = ResourceType.DOCUMENT
    proxy: str | None = None
    attempt: int = Field(default=1, ge=1)
    retry_of: str | None = None  # event_id of the first attempt
    headers: dict[str, str] = Field(default_factory=dict)
    cookie_names: list[str] = Field(default_factory=list)
    guard: GuardResult = Field(default_factory=GuardResult)
    cache: CacheInfo = Field(default_factory=CacheInfo)
    error: ErrorInfo | None = None
    integration: str = "manual"
    cost: float | None = None  # filled by the cost meter, in the configured currency
    monitor_error: str | None = None  # set when a hook failed while building this event


class Fix(_Model):
    """One change a normalizer made to one field. Nothing changes silently."""

    field: str
    normalizer: str
    before: Any = None
    after: Any = None


class Finding(_Model):
    """One problem found by the guard, the checks engine or a normalizer."""

    finding_id: str = Field(default_factory=_new_id)
    run_id: str
    scope: FindingScope
    ref: str | None = None  # event_id, record_id, or None for batch/run
    check: str  # dotted id, e.g. "field.required", "batch.row_count", "clean.price.ambiguous"
    severity: Severity = Severity.WARN
    field: str | None = None
    message: str
    observed: Any = None
    expected: Any = None
    rule_id: str | None = None


class Record(_Model):
    """One scraped item after checks and cleaning."""

    schema_version: int = SCHEMA_VERSION
    record_id: str = Field(default_factory=_new_id)
    run_id: str
    source_event_id: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    findings: list[Finding] = Field(default_factory=list)
    fixes: list[Fix] = Field(default_factory=list)
    usable: bool = True  # False when any finding has severity hold, or schema failed
    dedupe_key: str | None = None
    # From a page the conditional cache found unchanged (item 22): still counted in the row
    # count, left out of the changed-share check (it cannot have changed).
    unchanged: bool = False


class CostBreakdown(_Model):
    """Money spent in one run. Formulas: docs/architecture.md "Cost model"."""

    currency: str = "USD"
    total: float = 0.0
    by_domain: dict[str, float] = Field(default_factory=dict)
    by_proxy: dict[str, float] = Field(default_factory=dict)
    by_resource_type: dict[str, float] = Field(default_factory=dict)
    browser_minutes: float = 0.0
    browser_cost: float = 0.0
    api_requests: int = 0
    api_cost: float = 0.0
    usable_records: int = 0
    per_usable_record: float | None = None  # None when usable_records == 0
    per_record: float | None = None
    # Set only when the per-record figure is below PER_1000_BELOW (0.01 in the run's currency):
    # tiny numbers read better per 1,000 records, and then this is the headline figure.
    per_1000_usable_records: float | None = None
    per_1000_records: float | None = None

    def headline(self, *, usable: bool = True) -> tuple[float, str] | None:
        """(value, unit) to show: per 1,000 records when small, else per record."""
        per_1000 = self.per_1000_usable_records if usable else self.per_1000_records
        single = self.per_usable_record if usable else self.per_record
        noun = "usable records" if usable else "records"
        if per_1000 is not None:
            return per_1000, f"per 1,000 {noun}"
        if single is not None:
            return single, f"per {noun[:-1]}"
        return None


PER_1000_BELOW = 0.01  # below this per-record cost, the headline is per 1,000 records


def per_1000(per_record: float | None) -> float | None:
    """Cost per 1,000 records when the per-record cost is below ``PER_1000_BELOW``."""
    if per_record is None or per_record >= PER_1000_BELOW:
        return None
    return per_record * 1000


class WasteBreakdown(_Model):
    """Bytes and money that bought nothing usable."""

    bytes_by_resource_type: dict[str, int] = Field(default_factory=dict)
    retry_requests: int = 0
    retry_bytes: int = 0
    retry_cost: float = 0.0
    failed_requests: int = 0
    failed_bytes: int = 0
    failed_cost: float = 0.0
    unchanged_refetch_requests: int = 0
    unchanged_refetch_bytes: int = 0
    unchanged_refetch_cost: float = 0.0
    blockable_requests: int = 0  # image/media/font requests that were not blocked
    blockable_bytes: int = 0
    blockable_cost: float = 0.0
    total_waste_cost: float = 0.0
    # Waste cutter accounting (only when enabled)
    blocked_requests: int = 0
    bytes_saved_estimate: int = 0
    deduped_requests: int = 0
    conditional_hits: int = 0


class Verdict(_Model):
    """Per-run outcome and the reasons, in plain words."""

    status: VerdictStatus = VerdictStatus.PASS
    reasons: list[str] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)
    thresholds: dict[str, Any] = Field(default_factory=dict)
    baseline_runs: list[str] = Field(default_factory=list)


class RunCounts(_Model):
    """Counters kept during a run."""

    responses: int = 0
    document_responses: int = 0
    bad_documents: int = 0  # document responses with a label in BAD_LABELS
    by_label: dict[str, int] = Field(default_factory=dict)
    by_status: dict[str, int] = Field(default_factory=dict)
    retries: int = 0
    records: int = 0
    usable_records: int = 0
    duplicates: int = 0
    schema_invalid: int = 0  # records with at least one schema.invalid finding
    findings_by_severity: dict[str, int] = Field(default_factory=dict)
    fixes_by_normalizer: dict[str, int] = Field(default_factory=dict)
    flags: int = 0  # values a normalizer could not fix (clean.* findings above info)
    monitor_errors: int = 0
    wire_bytes: int = 0
    by_domain: dict[str, int] = Field(default_factory=dict)  # responses per domain
    by_bytes_source: dict[str, int] = Field(default_factory=dict)  # how wire_bytes was measured
    events_stored: int = 0  # events written to history by this process
    unchanged_records: int = 0  # records from pages the conditional cache found unchanged


class FindingSample(_Model):
    """Record findings of one kind in a run: how many, and a few observed values."""

    check: str
    field: str | None = None
    severity: Severity
    count: int = 0
    examples: list[Any] = Field(default_factory=list)  # at most DataRules.examples


class FixSample(_Model):
    """Fixes one normalizer made to one field in a run, with a few before/after pairs."""

    normalizer: str
    field: str
    count: int = 0
    examples: list[dict[str, Any]] = Field(default_factory=list)  # {"before": .., "after": ..}


class RecordSamples(_Model):
    """What the report shows about records without keeping them (architecture 16)."""

    findings: list[FindingSample] = Field(default_factory=list)
    fixes: list[FixSample] = Field(default_factory=list)


class Run(_Model):
    """Summary of one scraper run. Serialised as summary.json and stored in history."""

    schema_version: int = SCHEMA_VERSION
    run_id: str = Field(default_factory=_new_id)
    name: str = "default"
    started_at: datetime = Field(default_factory=_now)
    finished_at: datetime | None = None
    status: RunStatus = RunStatus.RUNNING
    integration: str = "manual"
    scrapewatch_version: str = ""
    rules_version: str | None = None
    config_hash: str | None = None
    counts: RunCounts = Field(default_factory=RunCounts)
    cost: CostBreakdown = Field(default_factory=CostBreakdown)
    waste: WasteBreakdown = Field(default_factory=WasteBreakdown)
    verdict: Verdict = Field(default_factory=Verdict)
    findings: list[Finding] = Field(default_factory=list)  # batch- and run-scope findings only
    samples: RecordSamples = Field(default_factory=RecordSamples)  # record findings and fixes
    report_path: str | None = None
    overhead_ms_p50: float | None = None
    overhead_ms_p95: float | None = None


__all__ = [
    "BAD_LABELS",
    "GUARD_LABEL_PRECEDENCE",
    "SCHEMA_VERSION",
    "BytesSource",
    "CacheInfo",
    "CostBreakdown",
    "ErrorInfo",
    "Finding",
    "FindingSample",
    "FindingScope",
    "Fix",
    "FixSample",
    "GuardLabel",
    "GuardResult",
    "Record",
    "RecordSamples",
    "ResourceType",
    "Run",
    "RunCounts",
    "RunStatus",
    "Severity",
    "Verdict",
    "VerdictStatus",
    "WasteBreakdown",
]
