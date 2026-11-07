"""Rule evaluation. Precedence and tie-breaking: docs/architecture.md "Response guard"."""

from __future__ import annotations

from collections.abc import Mapping

from scrapewatch.guard.features import Features, extract_features
from scrapewatch.guard.rules import GuardRule, GuardRuleSet, Signal, bundled_rules
from scrapewatch.models import GUARD_LABEL_PRECEDENCE, GuardLabel, GuardResult

_RANK = {label: i for i, label in enumerate(GUARD_LABEL_PRECEDENCE)}
# Signals computed from the body; none of them matches when the body was never read.
_BODY_SIGNALS = (
    "body_marker",
    "body_regex",
    "title_regex",
    "size_below",
    "size_below_baseline_pct",
    "text_density_below",
    "text_length_below",
    "has_form_password",
    "has_captcha_widget",
)


def _fmt(value: float) -> str:
    return f"{value:g}"


def _signal(sig: Signal, f: Features, rules: GuardRuleSet, lower_window: str | None) -> str | None:
    """Description of the signal when it matches, else None."""
    if sig.status is not None:
        return f"status in {sig.status}" if f.status in sig.status else None
    if sig.header is not None:
        name = sig.header.get("name", "").lower()
        value = f.headers.get(name)
        if value is None:
            return None
        if "regex" in sig.header:
            regex = sig.header["regex"]
            return f"header {name} ~ {regex}" if rules.pattern(regex).search(value) else None
        return f"header {name} present"
    if sig.cookie is not None:
        pat = rules.pattern(sig.cookie)
        return f"cookie ~ {sig.cookie}" if any(pat.search(c) for c in f.cookie_names) else None
    if not f.body_seen and any(getattr(sig, name) is not None for name in _BODY_SIGNALS):
        return None  # the body was never read: no evidence either way
    if sig.body_marker is not None:
        marker = sig.body_marker.lower()
        searched = f.scanned_markers is not None and marker in f.scanned_markers
        found = marker in f.markers or (
            not searched and lower_window is not None and marker in lower_window
        )
        return f"marker {marker}" if found else None
    if sig.body_regex is not None:
        hit = f.body_window is not None and rules.pattern(sig.body_regex).search(f.body_window)
        return f"body ~ {sig.body_regex}" if hit else None
    if sig.title_regex is not None:
        hit = f.title is not None and rules.pattern(sig.title_regex).search(f.title)
        return f"title ~ {sig.title_regex}" if hit else None
    if sig.final_url_regex is not None:
        moved = f.final_url is not None and f.final_url != f.request_url
        hit = moved and rules.pattern(sig.final_url_regex).search(f.final_url or "")
        return f"final url ~ {sig.final_url_regex}" if hit else None
    if sig.content_type is not None:
        ok = f.content_type is not None and f.content_type.startswith(sig.content_type.lower())
        return f"content-type {sig.content_type}*" if ok else None
    if sig.size_below is not None:
        return f"size {f.body_bytes} < {sig.size_below}" if f.body_bytes < sig.size_below else None
    if sig.size_below_baseline_pct is not None:
        base = f.baseline_p50_bytes
        if not base:
            return None
        limit = base * sig.size_below_baseline_pct / 100
        if f.body_bytes < limit:
            return f"size {f.body_bytes} < {sig.size_below_baseline_pct}% of baseline {base}"
        return None
    if sig.text_density_below is not None:
        if f.text_density < sig.text_density_below:
            return f"text density {_fmt(f.text_density)} < {_fmt(sig.text_density_below)}"
        return None
    if sig.text_length_below is not None:
        if f.text_length < sig.text_length_below:
            return f"text length {f.text_length} < {sig.text_length_below}"
        return None
    if sig.has_form_password is not None:
        if f.has_form_password == sig.has_form_password:
            return "password form" if sig.has_form_password else "no password form"
        return None
    if sig.has_captcha_widget is not None:
        if f.has_captcha_widget == sig.has_captcha_widget:
            return "captcha widget" if sig.has_captcha_widget else "no captcha widget"
        return None
    return None  # pragma: no cover - the loader rejects empty signals


def match_rule(
    rule: GuardRule, features: Features, rules: GuardRuleSet, lower_window: str | None = None
) -> list[str] | None:
    """Signals of the first matching group of a rule, or None when no group matches."""
    for group in rule.any:
        found: list[str] = []
        for sig in group.all:
            desc = _signal(sig, features, rules, lower_window)
            if desc is None:
                break
            found.append(desc)
        else:
            return found
    return None


def classify_features(features: Features, rules: GuardRuleSet) -> GuardResult:
    """Evaluate every rule; pick by label precedence, then confidence, then rule order."""
    lower_window = features.body_window.lower() if features.body_window else None
    best: tuple[int, float, int] | None = None
    result: GuardResult | None = None
    for order, rule in enumerate(rules.rules):
        signals = match_rule(rule, features, rules, lower_window)
        if signals is None:
            continue
        key = (_RANK[rule.label], -rule.confidence, order)
        if best is None or key < best:
            best = key
            result = GuardResult(
                label=rule.label,
                vendor=rule.vendor,
                rule_id=rule.id,
                confidence=rule.confidence,
                signals=signals,
            )
    if result is not None:
        return result
    if features.transport_error or features.status is None:
        return GuardResult(label=GuardLabel.ERROR, signals=["transport error"])
    if features.decode_error:
        return GuardResult(label=GuardLabel.UNKNOWN, signals=["body could not be decoded"])
    return GuardResult()


def classify(
    *,
    status: int | None,
    headers: Mapping[str, str],
    body: bytes | str | None = None,
    url: str | None = None,
    final_url: str | None = None,
    rules: GuardRuleSet | None = None,
    baseline_p50_bytes: int | None = None,
) -> GuardResult:
    """Convenience: extract features and classify with the bundled rules."""
    ruleset = rules if rules is not None else bundled_rules()
    features = extract_features(
        status=status,
        headers=headers,
        body=body,
        request_url=url,
        final_url=final_url,
        baseline_p50_bytes=baseline_p50_bytes,
        markers=ruleset.markers(),
    )
    return classify_features(features, ruleset)
