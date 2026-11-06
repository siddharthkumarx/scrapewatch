"""Import rules from docs/architecture.md section 4, checked by parsing every module with ast."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "scrapewatch"

# Top-level module or package (under scrapewatch.) -> layer.
LAYERS: dict[str, int] = {
    "_version": 0,
    "_yaml": 0,
    "errors": 0,
    "models": 0,
    "config": 0,
    "redact": 0,
    "history": 1,
    "cost": 1,
    "guard": 1,
    "clean": 1,
    "checks": 1,
    "io": 1,
    "gate": 2,
    "report": 2,
    "alerts": 2,
    "runtime": 3,
    "integrations": 4,
    "waste": 4,
    "cli": 4,
}
# L0 may import only these third-party packages (PyYAML for config loading).
L0_THIRD_PARTY = {"pydantic", "yaml"}
FRAMEWORKS = {
    "httpx",
    "requests",
    "urllib3",
    "playwright",
    "scrapy",
    "twisted",
    "crawlee",
    "pyarrow",
}
NETWORK_MODULES = {"urllib.request", "smtplib", "socket", "http.client", "ftplib"}


def _module_name(path: Path) -> str:
    rel = path.relative_to(SRC.parent).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _modules() -> list[Path]:
    return sorted(p for p in SRC.rglob("*.py"))


def _imports(path: Path, *, top_level_only: bool = False, name: str | None = None) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    package = name or _module_name(path)
    if path.name != "__init__.py":
        package = package.rsplit(".", 1)[0]
    nodes = tree.body if top_level_only else list(ast.walk(tree))
    out: list[str] = []
    for node in nodes:
        if isinstance(node, ast.Import):
            out.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")
                base = base[: len(base) - (node.level - 1)]
                mod = ".".join([*base, node.module] if node.module else base)
            else:
                mod = node.module or ""
            if mod != "scrapewatch":  # "from scrapewatch import x" is judged by x
                out.append(mod)
            out.extend(f"{mod}.{alias.name}" for alias in node.names)
    return out


def _layer(module: str) -> int | None:
    parts = module.split(".")
    if parts[0] != "scrapewatch" or len(parts) < 2:
        return None
    return LAYERS.get(parts[1])


def _stdlib(name: str) -> bool:
    return name.split(".")[0] in sys.stdlib_module_names or name == "__future__"


MODULES = [p for p in _modules() if _module_name(p) != "scrapewatch"]


def test_every_module_has_a_layer() -> None:
    missing = [_module_name(p) for p in MODULES if _layer(_module_name(p)) is None]
    assert missing == []


def _upward(path: Path, name: str) -> list[str]:
    my_layer = _layer(name)
    assert my_layer is not None
    upward = []
    for imp in _imports(path, name=name):
        layer = _layer(imp)
        if (layer is not None and layer > my_layer) or imp == "scrapewatch":
            upward.append(imp)  # the package root re-exports every layer
    return upward


@pytest.mark.parametrize("path", MODULES, ids=[_module_name(p) for p in MODULES])
def test_no_upward_imports(path: Path) -> None:
    me = _module_name(path)
    assert _upward(path, me) == [], f"{me} imports from a higher layer"


@pytest.mark.parametrize(
    "path",
    [p for p in MODULES if _layer(_module_name(p)) == 0],
    ids=lambda p: _module_name(p),
)
def test_layer0_third_party(path: Path) -> None:
    bad = [
        imp
        for imp in _imports(path)
        if not imp.startswith("scrapewatch")
        and not _stdlib(imp)
        and imp.split(".")[0] not in L0_THIRD_PARTY
    ]
    assert bad == []


@pytest.mark.parametrize("path", MODULES, ids=[_module_name(p) for p in MODULES])
def test_frameworks_imported_lazily(path: Path) -> None:
    eager = [i for i in _imports(path, top_level_only=True) if i.split(".")[0] in FRAMEWORKS]
    assert eager == [], f"{_module_name(path)} imports a framework at module level: {eager}"


@pytest.mark.parametrize("path", MODULES, ids=[_module_name(p) for p in MODULES])
def test_network_io_only_in_alerts(path: Path) -> None:
    if _module_name(path).startswith("scrapewatch.alerts"):
        return
    net = [
        i for i in _imports(path) if any(i == n or i.startswith(n + ".") for n in NETWORK_MODULES)
    ]
    assert net == []


def test_layering_detects_violations(tmp_path: Path) -> None:
    """The checker itself reports upward imports (absolute, relative and the package root)."""
    sample = tmp_path / "redact.py"
    sample.write_text(
        "from scrapewatch.runtime import Watch\nfrom . import gate\nimport scrapewatch\n"
        "from scrapewatch import errors\n",
        "utf-8",
    )
    assert _upward(sample, "scrapewatch.redact") == [
        "scrapewatch.runtime",
        "scrapewatch.runtime.Watch",
        "scrapewatch.gate",
        "scrapewatch",
    ]
