"""Safe expression subset for cross-field rules (no eval).

Grammar: comparisons, ``and``/``or``/``not``, ``+ - * /``, names (fields), constants,
``is None`` / ``is not None``, and calls to ``len``, ``abs``, ``min``, ``max``, ``round``.
The expression is parsed with :mod:`ast`, every node is checked against that whitelist at
load time, and a small interpreter evaluates the tree; nothing is ever passed to ``eval``.
"""

from __future__ import annotations

import ast
import operator
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from scrapewatch.errors import RulesError

ALLOWED_CALLS: frozenset[str] = frozenset({"len", "abs", "min", "max", "round"})
MAX_EXPR_LENGTH = 500

_CALLS: dict[str, Callable[..., Any]] = {
    "len": len,
    "abs": abs,
    "min": min,
    "max": max,
    "round": round,
}
_BINOPS: dict[type[ast.operator], Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}
_CMPOPS: dict[type[ast.cmpop], Callable[[Any, Any], Any]] = {
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.Is: operator.is_,
    ast.IsNot: operator.is_not,
}


@dataclass(frozen=True)
class CompiledExpr:
    """A validated expression tree."""

    source: str
    tree: ast.expr


def _reject(node: ast.AST, source: str, why: str) -> RulesError:
    return RulesError(f"expression {source!r}: {why} ({type(node).__name__} is not allowed)")


def _check(node: ast.AST, source: str) -> None:
    if isinstance(node, ast.BoolOp):
        if not isinstance(node.op, (ast.And, ast.Or)):  # pragma: no cover - only and/or exist
            raise _reject(node, source, "unknown boolean operator")
        for v in node.values:
            _check(v, source)
    elif isinstance(node, ast.UnaryOp):
        if not isinstance(node.op, (ast.Not, ast.USub, ast.UAdd)):
            raise _reject(node.op, source, "only not, unary - and + are allowed")
        _check(node.operand, source)
    elif isinstance(node, ast.BinOp):
        if type(node.op) not in _BINOPS:
            raise _reject(node.op, source, "only + - * / are allowed")
        _check(node.left, source)
        _check(node.right, source)
    elif isinstance(node, ast.Compare):
        _check(node.left, source)
        for op, right in zip(node.ops, node.comparators, strict=True):
            if type(op) not in _CMPOPS:
                raise _reject(op, source, "only < <= == != >= > is and is not are allowed")
            if isinstance(op, (ast.Is, ast.IsNot)) and not (
                isinstance(right, ast.Constant) and right.value is None
            ):
                raise _reject(op, source, "'is' / 'is not' may only compare with None")
            _check(right, source)
    elif isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in ALLOWED_CALLS:
            raise _reject(node, source, f"only calls to {', '.join(sorted(ALLOWED_CALLS))}")
        if node.keywords or any(isinstance(a, ast.Starred) for a in node.args):
            raise _reject(node, source, "calls take plain positional arguments only")
        for a in node.args:
            _check(a, source)
    elif isinstance(node, ast.Name):
        if not isinstance(node.ctx, ast.Load):  # pragma: no cover - eval mode only loads
            raise _reject(node, source, "names can only be read")
        if node.id.startswith("__"):
            raise _reject(node, source, "dunder names are not fields")
    elif isinstance(node, ast.Constant):
        if not (node.value is None or isinstance(node.value, (bool, int, float, str))):
            raise _reject(node, source, "only str, number, bool and None constants")
    else:
        raise _reject(node, source, "outside the safe subset")


def compile_expr(expr: str) -> Any:
    """Parse with ``ast`` and reject any node outside the subset; returns a compiled object."""
    if not isinstance(expr, str) or not expr.strip():
        raise RulesError("expression must be a non-empty string")
    if len(expr) > MAX_EXPR_LENGTH:
        raise RulesError(f"expression longer than {MAX_EXPR_LENGTH} characters")
    try:
        tree = ast.parse(expr.strip(), mode="eval")
    except SyntaxError as exc:
        raise RulesError(f"expression {expr!r} is not valid: {exc.msg}") from exc
    _check(tree.body, expr)
    return CompiledExpr(source=expr, tree=tree.body)


def _eval(node: ast.expr, data: Mapping[str, Any]) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return data.get(node.id)
    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            result: Any = True
            for v in node.values:
                result = _eval(v, data)
                if not result:
                    return result
            return result
        result = False
        for v in node.values:
            result = _eval(v, data)
            if result:
                return result
        return result
    if isinstance(node, ast.UnaryOp):
        operand = _eval(node.operand, data)
        if isinstance(node.op, ast.Not):
            return not operand
        return -operand if isinstance(node.op, ast.USub) else +operand
    if isinstance(node, ast.BinOp):
        return _BINOPS[type(node.op)](_eval(node.left, data), _eval(node.right, data))
    if isinstance(node, ast.Compare):
        left = _eval(node.left, data)
        for op, comparator in zip(node.ops, node.comparators, strict=True):
            right = _eval(comparator, data)
            if not _CMPOPS[type(op)](left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        return _CALLS[node.func.id](*(_eval(a, data) for a in node.args))
    raise RulesError(f"cannot evaluate {type(node).__name__}")  # pragma: no cover


def evaluate(compiled: Any, data: Mapping[str, Any]) -> bool:
    """Evaluate against one record; missing fields are None.

    Errors of the expression itself on this record (comparing None with a number, dividing by
    zero) propagate as the usual ``TypeError`` / ``ZeroDivisionError``; the engine reports them.
    """
    if not isinstance(compiled, CompiledExpr):
        compiled = compile_expr(str(compiled))
    return bool(_eval(compiled.tree, data))
