"""Safe expression subset for cross-field rules (no eval).

Grammar: comparisons, ``and``/``or``/``not``, ``+ - * /``, names (fields), constants,
``is None`` / ``is not None``, and calls to ``len``, ``abs``, ``min``, ``max``, ``round``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

ALLOWED_CALLS: frozenset[str] = frozenset({"len", "abs", "min", "max", "round"})


def compile_expr(expr: str) -> Any:
    """Parse with ``ast`` and reject any node outside the subset; returns a compiled object."""
    raise NotImplementedError("build-progress item 7")


def evaluate(compiled: Any, data: Mapping[str, Any]) -> bool:
    """Evaluate against one record; missing fields are None."""
    raise NotImplementedError("build-progress item 7")
