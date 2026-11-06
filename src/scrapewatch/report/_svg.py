"""Inline SVG for the report: sparklines and bars. No scripts, no external resources."""

from __future__ import annotations

from collections.abc import Sequence
from html import escape


def _fmt(x: float) -> str:
    return f"{x:.1f}".rstrip("0").rstrip(".")


def sparkline(
    values: Sequence[float | None],
    *,
    mark: int | None = None,
    label: str = "",
    width: int = 220,
    height: int = 40,
) -> str:
    """A polyline over ``values`` (None leaves a gap); the point at ``mark`` is highlighted."""
    pad = 4
    known = [v for v in values if v is not None]
    title = f"<title>{escape(label)}</title>" if label else ""
    if not known:
        return (
            f'<svg class="spark" role="img" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}">{title}'
            f'<text x="{pad}" y="{height // 2 + 4}" class="muted">no data yet</text></svg>'
        )
    lo, hi = min(known), max(known)
    span = (hi - lo) or 1.0
    step = (width - 2 * pad) / max(len(values) - 1, 1)

    def point(i: int, v: float) -> tuple[float, float]:
        x = pad + i * step if len(values) > 1 else width / 2
        y = height - pad - (v - lo) / span * (height - 2 * pad)
        return x, y

    lines: list[str] = []
    run: list[str] = []
    for i, v in enumerate(values):
        if v is None:
            if len(run) > 1:
                lines.append(" ".join(run))
            run = []
            continue
        x, y = point(i, v)
        run.append(f"{_fmt(x)},{_fmt(y)}")
    if len(run) > 1:
        lines.append(" ".join(run))
    parts = [
        f'<svg class="spark" role="img" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">{title}'
    ]
    parts += [f'<polyline points="{pts}" class="line"/>' for pts in lines]
    for i, v in enumerate(values):
        if v is not None and (i == mark or len(known) == 1):
            x, y = point(i, v)
            css = "dot now" if i == mark else "dot"
            parts.append(f'<circle cx="{_fmt(x)}" cy="{_fmt(y)}" r="3" class="{css}"/>')
    parts.append("</svg>")
    return "".join(parts)


def bar(value: float, maximum: float, *, kind: str = "", width: int = 160) -> str:
    """A horizontal bar of ``value / maximum`` of ``width`` pixels."""
    share = 0.0 if maximum <= 0 else max(0.0, min(1.0, value / maximum))
    filled = round(share * width, 1)
    css = f"bar {kind}".strip()
    return (
        f'<svg class="barsvg" width="{width}" height="10" viewBox="0 0 {width} 10" '
        f'aria-hidden="true"><rect width="{width}" height="10" class="track"/>'
        f'<rect width="{_fmt(filled)}" height="10" class="{css}"/></svg>'
    )
