"""YAML loading shared by the config, data-rules and guard-rules loaders.

``yaml.safe_load`` follows YAML 1.1, where ``on``, ``off``, ``yes`` and ``no`` are booleans;
that turns the documented ``alerts.on`` key into ``True``. This loader keeps the safe loader
but resolves only ``true``/``false`` (any case) as booleans, as YAML 1.2 does.
"""

from __future__ import annotations

import re
from typing import Any

import yaml


class _Loader(yaml.SafeLoader):
    """SafeLoader with YAML 1.2 booleans."""


_Loader.yaml_implicit_resolvers = {
    first: [(tag, rx) for tag, rx in resolvers if tag != "tag:yaml.org,2002:bool"]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_Loader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
    list("tTfF"),
)


def _construct_bool(loader: yaml.SafeLoader, node: yaml.Node) -> bool:
    value = loader.construct_scalar(node)  # type: ignore[arg-type]
    return str(value).lower() == "true"


_Loader.add_constructor("tag:yaml.org,2002:bool", _construct_bool)


def safe_load(text: str) -> Any:
    """Parse YAML text safely (no Python objects) with YAML 1.2 booleans."""
    return yaml.load(text, Loader=_Loader)  # noqa: S506 - _Loader derives from SafeLoader
