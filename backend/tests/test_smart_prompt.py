"""Tests for backend.normalizer.smart.build_prompt (021E-1)."""
from __future__ import annotations

from backend.normalizer.smart import (
    _canonical_field_names,
    build_prompt,
)


def test_build_prompt_returns_system_and_user_strings():
    sys, usr = build_prompt(
        source_name="src",
        samples=[{"a": "v"}],
        raw_fields=["a"],
        canonical_fields=["title"],
    )
    assert isinstance(sys, str) and sys
    assert isinstance(usr, str) and usr


def test_build_prompt_includes_source_name():
    sys, usr = build_prompt("MySource", [], ["x"], ["title"])
    assert "MySource" in usr


def test_build_prompt_includes_canonical_and_raw_fields():
    sys, usr = build_prompt("s", [{"foo": 1}], ["foo", "bar"], ["title", "indicator"])
    assert "foo" in usr and "bar" in usr
    assert "title" in usr and "indicator" in usr


def test_build_prompt_lists_skip_token():
    sys, usr = build_prompt("s", [], ["x"], ["title"])
    assert "__skip__" in sys
    assert "__skip__" in usr


def test_build_prompt_is_field_centric_with_selective_examples():
    # prompts-061: the prompt is field-centric. A field whose name does NOT
    # match a canonical name is ambiguous and gets inline example values; a
    # field whose leaf name matches a canonical name is self-describing and is
    # listed without examples.
    samples = [{"weird_col": "v1", "title": "T1"}, {"weird_col": "v2", "title": "T2"}]
    sys, usr = build_prompt("s", samples, ["weird_col", "title"], ["title", "indicator"])
    assert "weird_col" in usr
    # Ambiguous field carries example values.
    assert "e.g." in usr
    assert "v1" in usr or "v2" in usr
    # Self-describing field line has no example marker.
    title_line = next(
        ln for ln in usr.splitlines() if ln.strip() == "- title"
    )
    assert "e.g." not in title_line
    # No full-row JSON sample block is emitted any more.
    assert '{"title"' not in usr and '{"weird_col"' not in usr


def test_build_prompt_lists_real_field_names_per_record():
    # prompts-063: the signature-collapse (UUID/hex/numeric → "*") was removed.
    # Each record now flattens to ordinary field names which are listed as-is;
    # the prompt no longer contains any "*" wildcard placeholder.
    raw = ["Orgc.name", "Tag.colour", "info", "date"]
    samples = [{"Orgc.name": "Org", "Tag.colour": "#fff", "info": "x", "date": "2026"}]
    sys, usr = build_prompt("s", samples, raw, ["title"])
    assert "Orgc.name" in usr
    assert "Tag.colour" in usr
    assert "info" in usr
    # No structural-signature wildcard language remains.
    assert "*" not in usr
    assert "wildcard" not in usr.lower()


def test_canonical_field_names_nonempty_and_includes_yaml_canonicals():
    names = _canonical_field_names()
    assert isinstance(names, list)
    assert len(names) > 0
    # 'title' is one of the most basic yaml core fields.
    assert "title" in names


def test_canonical_field_names_configured_filters_disabled(tmp_path, monkeypatch):
    """field_scope='configured' returns only ENABLED fields; 'all' returns
    every field regardless of its enabled flag (prompts-032 Phase E)."""
    import backend.config.loader as loader
    p = tmp_path / "feed-fields.yaml"
    p.write_text(
        "core_fields:\n"
        "  - name: indicator\n"
        "    enabled: true\n"
        "  - name: severity\n"
        "    enabled: false\n"
        "custom_fields:\n"
        "  - name: vendor_score\n"
        "    enabled: true\n"
    )
    monkeypatch.setattr(loader, "FIELDS_PATH", p)
    assert _canonical_field_names("configured") == ["indicator", "vendor_score"]
    # default 'all' keeps the disabled field too.
    assert _canonical_field_names("all") == ["indicator", "severity", "vendor_score"]
    assert _canonical_field_names() == ["indicator", "severity", "vendor_score"]

