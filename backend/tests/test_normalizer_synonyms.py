"""Declarative sanity checks for the normalizer synonym tables (prompts-021C).

These tests assert structural invariants over `_SYNONYM_GROUPS` and
`_WILDCARD_PATTERNS` so future edits cannot silently break the resolver.

021E-pre adds an invariant that engine canonicals must exist in
config/feed-fields.yaml — the engine and the rest of the system share one
canonical namespace.
"""
from __future__ import annotations


def test_no_duplicate_wildcard_patterns():
    from backend.normalizer.engine import _WILDCARD_PATTERNS
    seen: set[str] = set()
    for pattern, _canonical in _WILDCARD_PATTERNS:
        assert pattern not in seen, f"duplicate wildcard pattern: {pattern!r}"
        seen.add(pattern)


def test_every_wildcard_canonical_exists_in_engine():
    """Q-A=C invariant: wildcards may only target canonicals already present
    in _SYNONYM_GROUPS."""
    from backend.normalizer.engine import _SYNONYM_GROUPS, _WILDCARD_PATTERNS
    engine_canonicals = {c for c, _ in _SYNONYM_GROUPS}
    for pattern, canonical in _WILDCARD_PATTERNS:
        assert canonical in engine_canonicals, (
            f"wildcard {pattern!r} → {canonical!r} not in engine canonicals"
        )


def test_no_wildcard_targets_severity():
    """Deliberate omission (Q-CRITICAL-2=A): the `severity` synonym group is
    reserved for pure severity-level terms. Numeric scoring routes to
    `cvss_score`; adding severity wildcards would re-introduce that overload."""
    from backend.normalizer.engine import _WILDCARD_PATTERNS
    offenders = [p for p, c in _WILDCARD_PATTERNS if c == "severity"]
    assert offenders == [], (
        f"021C deliberately omits severity wildcards; found: {offenders!r}"
    )


def test_specificity_function_ranks_correctly():
    from backend.normalizer.engine import _specificity
    assert _specificity("cve") == 0          # exact, no glob
    assert _specificity("cve*") == 1         # anchored at end
    assert _specificity("*cve") == 1         # anchored at start
    assert _specificity("*cve*") == 2        # free glob (both ends)
    assert _specificity("c*e*id") == 2       # free glob (interior)


def test_patterns_are_lowercase_and_stripped():
    from backend.normalizer.engine import _WILDCARD_PATTERNS
    for pattern, canonical in _WILDCARD_PATTERNS:
        assert pattern == pattern.lower(), f"pattern not lowercase: {pattern!r}"
        assert pattern == pattern.strip(), f"pattern has whitespace: {pattern!r}"
        assert canonical == canonical.lower(), f"canonical not lowercase: {canonical!r}"


# ── Q-PRE-6 invariant (021E-pre) ───────────────────────────────────────────────

def test_every_engine_canonical_exists_in_feed_fields_yaml():
    """Engine canonicals must be a subset of config/feed-fields.yaml.

    This guards against the very divergence 021E-pre was created to fix: if a
    future edit adds an engine canonical that yaml does not know about, the
    operator-visible field list will fall out of sync with normalized.db and
    smart-mode (021E) would lie to the LLM about the schema. Note the inverse
    direction (yaml-only fields) is allowed — yaml may declare canonicals the
    engine has no auto-synonyms for; operators can populate them via manual
    mappings or by raw key passthrough.
    """
    from backend.config.loader import load_fields
    from backend.normalizer.engine import _SYNONYM_GROUPS

    yaml_data = load_fields() or {}
    yaml_names: set[str] = set()
    for group in ("core_fields", "custom_fields"):
        for field in yaml_data.get(group, []) or []:
            name = (field or {}).get("name")
            if name:
                yaml_names.add(name)

    engine_canonicals = {c for c, _ in _SYNONYM_GROUPS}
    missing = engine_canonicals - yaml_names
    assert missing == set(), (
        f"Engine canonicals not present in config/feed-fields.yaml: {sorted(missing)!r}. "
        "Either add the field to feed-fields.yaml or remove the synonym group."
    )
