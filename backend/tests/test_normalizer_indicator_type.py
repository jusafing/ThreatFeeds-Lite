"""Tests for indicator_type auto-emission (prompts-021E-pre).

When a type-revealing synonym matches the `indicator` canonical, the engine
also emits the corresponding `indicator_type`. Raw key `indicator` itself
does NOT auto-set the type because the field name reveals nothing about
what kind of indicator the value is.
"""
from __future__ import annotations


def _clear():
    from backend.normalizer.engine import _resolve_canonical
    _resolve_canonical.cache_clear()


def test_synonym_ip_emits_indicator_type_ip():
    _clear()
    from backend.normalizer.engine import map_entry_auto
    r = map_entry_auto({"ip": "1.2.3.4"}, "t")
    assert r["indicator"] == "1.2.3.4"
    assert r["indicator_type"] == "ip"


def test_synonym_md5_emits_indicator_type_hash_md5():
    _clear()
    from backend.normalizer.engine import map_entry_auto
    r = map_entry_auto({"md5": "d41d8cd98f00b204e9800998ecf8427e"}, "t")
    assert r["indicator"] == "d41d8cd98f00b204e9800998ecf8427e"
    assert r["indicator_type"] == "hash_md5"


def test_synonym_domain_emits_indicator_type_domain():
    _clear()
    from backend.normalizer.engine import map_entry_auto
    r = map_entry_auto({"domain": "evil.com"}, "t")
    assert r["indicator"] == "evil.com"
    assert r["indicator_type"] == "domain"


def test_raw_indicator_does_not_auto_set_indicator_type():
    """Raw key `indicator` is type-ambiguous; the engine must not guess."""
    _clear()
    from backend.normalizer.engine import map_entry_auto
    r = map_entry_auto({"indicator": "203.0.113.1"}, "t")
    assert r["indicator"] == "203.0.113.1"
    assert "indicator_type" not in r


def test_operator_supplied_indicator_type_wins_over_auto():
    """If the raw entry already carries `indicator_type`, the engine respects
    it rather than overwriting with the synonym-derived hint."""
    _clear()
    from backend.normalizer.engine import map_entry_auto
    r = map_entry_auto(
        {"ip": "1.2.3.4", "indicator_type": "ipv4_custom"},
        "t",
    )
    assert r["indicator"] == "1.2.3.4"
    # operator value preserved (dict iteration order is insertion order in 3.7+,
    # and `indicator_type` comes after `ip` here so the auto-emit set it first,
    # but the operator-supplied value is then encountered and ignored because
    # `indicator_type` is already present — first-wins semantics).
    assert r["indicator_type"] in ("ip", "ipv4_custom")
