import math

import pytest

from scrapewatch.checks.expr import compile_expr, evaluate
from scrapewatch.errors import RulesError

DATA = {
    "price": 10.0,
    "sale_price": 8.5,
    "qty": 3,
    "title": "A Light in the Attic",
    "currency": "GBP",
    "flag": True,
    "none_field": None,
    "zero": 0,
    "neg": -4,
}
CALLS = {"len": len, "abs": abs, "min": min, "max": max, "round": round}

ALLOWED = [
    "sale_price <= price",
    "sale_price < price and price > 0",
    "price == 10",
    "price != 11",
    "qty >= 3",
    "qty > 3 or qty < 3",
    "not flag",
    "not (price > 100)",
    "none_field is None",
    "price is not None",
    "missing_field is None",
    "price - sale_price < 2",
    "price * qty == 30",
    "price / 4 == 2.5",
    "price + sale_price > 18",
    "-neg == 4",
    "+qty == 3",
    "len(title) > 5",
    "len(title) <= 40 and len(currency) == 3",
    "abs(neg) == 4",
    "min(price, sale_price) == sale_price",
    "max(price, sale_price, 99) == 99",
    "round(sale_price) == 8",
    "round(price / 3, 2) == 3.33",
    "currency == 'GBP'",
    'title != ""',
    "0 < qty < 5",
    "zero or price",
    "flag and qty",
    "price >= 0 and (sale_price is None or sale_price <= price)",
]

DISALLOWED = [
    "__import__('os').system('true')",
    "price.real > 0",
    "(lambda: 1)()",
    "title[0] == 'A'",
    "[x for x in title]",
    "{x for x in title}",
    "eval('1')",
    "price if flag else 0",
    "currency in ['GBP']",
    "len(title, *[])",
    "open('f')",
    "price is 10",
]


def test_counts() -> None:
    assert len(ALLOWED) == 30
    assert len(DISALLOWED) == 12


@pytest.mark.parametrize("expr", ALLOWED)
def test_allowed_expressions_match_python(expr: str) -> None:
    compiled = compile_expr(expr)
    namespace = {**CALLS, **{k: DATA.get(k) for k in compile_names(expr)}}
    expected = bool(eval(expr, {"__builtins__": {}}, namespace))  # reference semantics
    assert evaluate(compiled, DATA) is expected


def compile_names(expr: str) -> set[str]:
    import ast

    return {
        n.id
        for n in ast.walk(ast.parse(expr, mode="eval"))
        if isinstance(n, ast.Name) and n.id not in CALLS
    }


@pytest.mark.parametrize("expr", DISALLOWED)
def test_disallowed_expressions_raise(expr: str) -> None:
    with pytest.raises(RulesError):
        compile_expr(expr)


@pytest.mark.parametrize("expr", ["", "   ", "price <", "x" * 501, "f(**k)"])
def test_invalid_source(expr: str) -> None:
    with pytest.raises(RulesError):
        compile_expr(expr)


def test_more_rejections() -> None:
    for expr in [
        "~qty",
        "price ** 2",
        "price // 2",
        "__class__ is None",
        "b'x' == b'x'",
        "min(x=1)",
        "f'{price}'",
    ]:
        with pytest.raises(RulesError):
            compile_expr(expr)


def test_errors_on_a_record_propagate() -> None:
    with pytest.raises(TypeError):
        evaluate(compile_expr("none_field < 3"), DATA)
    with pytest.raises(ZeroDivisionError):
        evaluate(compile_expr("price / zero > 1"), DATA)


def test_evaluate_accepts_source_text() -> None:
    assert evaluate("qty == 3", DATA) is True
    assert math.isclose(10.0, DATA["price"])  # type: ignore[arg-type]
