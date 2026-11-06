"""Dedupe key of a record (docs/architecture.md "Cleaning" 10.6). Not a field normalizer."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any


def _missing(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def dedupe_key(record: Mapping[str, Any], key_fields: Sequence[str]) -> str | None:
    """``sha256:`` + hex of the canonical JSON of the key fields (after cleaning).

    Returns None when ``key_fields`` is empty or any key field is missing; the engine then
    records a ``dup.key_missing`` finding.
    """
    if not key_fields or any(_missing(record.get(k)) for k in key_fields):
        return None
    payload = {k: record.get(k) for k in key_fields}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
