"""Tests for backend.normalizer.smart.sample_raw_entries + helpers (021E-1)."""
from __future__ import annotations

import pytest

from backend.normalizer import smart as smart_mod
from backend.normalizer.smart import (
    SmartModeError,
    _sanitise_row,
    _sanitise_value,
    discover_raw_field_names,
    sample_raw_entries,
)


# ── _sanitise_value ─────────────────────────────────────────────────────────


def test_sanitise_value_strips_control_chars():
    assert _sanitise_value("hello\x00\x01world") == "helloworld"


def test_sanitise_value_truncates_long_strings():
    big = "x" * 200
    out = _sanitise_value(big)
    assert isinstance(out, str)
    assert len(out) <= 80
    assert out.endswith("…")


def test_sanitise_value_preserves_scalars():
    assert _sanitise_value(42) == 42
    assert _sanitise_value(3.14) == 3.14
    assert _sanitise_value(True) is True
    assert _sanitise_value(None) is None


def test_sanitise_value_encodes_complex_objects():
    out = _sanitise_value({"k": "v"})
    assert isinstance(out, str)
    assert "k" in out and "v" in out


# ── _sanitise_row ───────────────────────────────────────────────────────────


def test_sanitise_row_drops_housekeeping_keys():
    row = {"id": 1, "ingested_at": "x", "normalized": 0, "title": "Bad\x01Wolf"}
    out = _sanitise_row(row)
    assert "id" not in out
    assert "ingested_at" not in out
    assert "normalized" not in out
    assert out["title"] == "BadWolf"


def test_sanitise_row_keeps_empty_valued_keys():
    # prompts-056: empty/None values are retained so the field name still
    # reaches discover_raw_field_names → the LLM prompt. Only housekeeping
    # keys are dropped.
    row = {"a": "x", "b": None, "c": ""}
    out = _sanitise_row(row)
    assert out == {"a": "x", "b": None, "c": ""}


def test_sanitise_row_keeps_field_empty_in_some_rows_across_sample():
    # A field present-but-empty in one row and populated in another must
    # appear in the unioned field list (regression: prompts-056).
    samples = [
        _sanitise_row({"title": "A", "cve": ""}),
        _sanitise_row({"title": "B", "cve": "CVE-2024-1"}),
    ]
    assert discover_raw_field_names(samples) == ["title", "cve"]


# ── discover_raw_field_names ────────────────────────────────────────────────


def test_discover_raw_fields_preserves_first_seen_order():
    samples = [{"a": 1, "b": 2}, {"c": 3, "a": 4}]
    assert discover_raw_field_names(samples) == ["a", "b", "c"]


def test_discover_raw_fields_empty_input():
    assert discover_raw_field_names([]) == []


# ── sample_raw_entries ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sample_raw_entries_rejects_zero_size():
    with pytest.raises(SmartModeError):
        await sample_raw_entries("any", sample_size=0)


@pytest.mark.asyncio
async def test_sample_raw_entries_caps_to_max(monkeypatch):
    captured = {}

    async def fake_qe(source_name, limit, filters=None):
        captured["limit"] = limit
        return [{"a": "v"}]

    async def fake_qn(*a, **kw):
        return []

    monkeypatch.setattr(smart_mod, "query_entries", fake_qe)
    monkeypatch.setattr(smart_mod, "query_normalized", fake_qn)
    out = await sample_raw_entries("src", sample_size=500)
    assert captured["limit"] == 100
    assert out == [{"a": "v"}]


@pytest.mark.asyncio
async def test_sample_raw_entries_falls_back_to_normalized(monkeypatch):
    async def fake_qe(*a, **kw):
        return []

    async def fake_qn(*a, **kw):
        return [{"title": "fallback"}]

    monkeypatch.setattr(smart_mod, "query_entries", fake_qe)
    monkeypatch.setattr(smart_mod, "query_normalized", fake_qn)
    out = await sample_raw_entries("src", sample_size=5)
    assert out == [{"title": "fallback"}]


@pytest.mark.asyncio
async def test_sample_raw_entries_raises_when_all_empty(monkeypatch):
    async def empty(*a, **kw):
        return []

    monkeypatch.setattr(smart_mod, "query_entries", empty)
    monkeypatch.setattr(smart_mod, "query_normalized", empty)
    with pytest.raises(SmartModeError, match="no entries"):
        await sample_raw_entries("src", sample_size=5)
