"""Tests for backend.query.nl — NL→filter translation + execution (prompts-064)."""
from __future__ import annotations

import asyncio

import pytest

from backend.query import nl
from backend.query.nl import (
    DEFAULT_DATASET,
    DEFAULT_LIMIT,
    MAX_LIMIT,
    NLQueryError,
    StructuredQuery,
    build_nl_prompt,
    execute_structured_query,
    parse_nl_filter,
    validate_nl_filter,
)


# ── build_nl_prompt ─────────────────────────────────────────────────────────

def test_build_nl_prompt_includes_question_and_allowed_keys():
    sys, usr = build_nl_prompt(
        "find critical CVEs", default_dataset="normalized", known_sources=["feedA"],
    )
    assert "find critical CVEs" in usr
    assert "feedA" in usr
    # the closed key set is advertised
    assert "dataset" in sys and "search" in sys and "severity" in sys
    # never asks for SQL
    assert "SQL" in sys  # mentioned only to forbid it
    assert "no SQL" in sys.lower() or "no sql" in sys.lower()


def test_build_nl_prompt_handles_no_sources():
    sys, usr = build_nl_prompt("x", default_dataset="raw", known_sources=[])
    assert "(none)" in usr


# ── parse_nl_filter ─────────────────────────────────────────────────────────

def test_parse_nl_filter_plain_json():
    assert parse_nl_filter('{"dataset": "raw", "search": "npm"}') == {
        "dataset": "raw", "search": "npm",
    }


def test_parse_nl_filter_code_fenced():
    text = '```json\n{"search": "log4j"}\n```'
    assert parse_nl_filter(text) == {"search": "log4j"}


def test_parse_nl_filter_raises_on_garbage():
    with pytest.raises(NLQueryError):
        parse_nl_filter("not json at all")


# ── validate_nl_filter ──────────────────────────────────────────────────────

def test_validate_drops_unknown_keys():
    sq = validate_nl_filter({"search": "x", "evil": "DROP TABLE", "sql": "..."})
    assert sq.search == "x"
    assert "evil" not in sq.column_filters and "sql" not in sq.column_filters


def test_validate_dataset_default_and_override():
    assert validate_nl_filter({}, default_dataset="raw").dataset == "raw"
    assert validate_nl_filter({"dataset": "normalized"}, default_dataset="raw").dataset == "normalized"
    # invalid dataset falls back to the default
    assert validate_nl_filter({"dataset": "bogus"}, default_dataset="raw").dataset == "raw"


def test_validate_unknown_default_dataset_falls_back():
    assert validate_nl_filter({}, default_dataset="weird").dataset == DEFAULT_DATASET


def test_validate_source_must_be_known():
    sq = validate_nl_filter(
        {"source": "../users"}, known_sources=["feedA", "feedB"],
    )
    assert sq.source is None  # unknown source dropped (path-traversal guard)
    sq2 = validate_nl_filter({"source": "feedA"}, known_sources=["feedA"])
    assert sq2.source == "feedA"


def test_validate_limit_clamped():
    assert validate_nl_filter({"limit": 0}).limit == 1
    assert validate_nl_filter({"limit": 999999}).limit == MAX_LIMIT
    assert validate_nl_filter({"limit": "50"}).limit == 50
    assert validate_nl_filter({"limit": "abc"}).limit == DEFAULT_LIMIT


def test_validate_column_filters_and_blank_drop():
    sq = validate_nl_filter({"severity": "critical", "actor": "", "country": "RU"})
    assert sq.column_filters == {"severity": "critical", "country": "RU"}


def test_interpreted_filter_round_trip():
    sq = validate_nl_filter(
        {"dataset": "raw", "source": "feedA", "search": "npm", "severity": "high"},
        known_sources=["feedA"],
    )
    interp = sq.as_interpreted_filter()
    assert interp["dataset"] == "raw"
    assert interp["source"] == "feedA"
    assert interp["search"] == "npm"
    assert interp["severity"] == "high"


# ── execute_structured_query ────────────────────────────────────────────────

def test_execute_raw_passes_filters(monkeypatch):
    captured = {}

    async def _fake_query_entries(**kwargs):
        captured.update(kwargs)
        return [{"indicator": "1.2.3.4"}]

    monkeypatch.setattr(nl, "query_entries", _fake_query_entries)
    sq = StructuredQuery(dataset="raw", source="feedA", search="npm",
                         limit=10, column_filters={"severity": "high"})
    rows = asyncio.run(execute_structured_query(sq))
    assert rows == [{"indicator": "1.2.3.4"}]
    assert captured["source_name"] == "feedA"
    assert captured["search"] == "npm"
    assert captured["limit"] == 10
    assert captured["filters"] == {"severity": "high"}


def test_execute_normalized_no_filters(monkeypatch):
    captured = {}

    async def _fake_query_normalized(**kwargs):
        captured.update(kwargs)
        return [{"indicator": "a"}, {"indicator": "b"}]

    monkeypatch.setattr(nl, "query_normalized", _fake_query_normalized)
    sq = StructuredQuery(dataset="normalized", search="cve", limit=5)
    rows = asyncio.run(execute_structured_query(sq))
    assert len(rows) == 2
    assert captured["limit"] == 5


def test_execute_normalized_post_filters_and_truncates(monkeypatch):
    async def _fake_query_normalized(**kwargs):
        return [
            {"indicator": "a", "severity": "critical"},
            {"indicator": "b", "severity": "low"},
            {"indicator": "c", "severity": "critical"},
            {"indicator": "d", "severity": "critical"},
        ]

    monkeypatch.setattr(nl, "query_normalized", _fake_query_normalized)
    sq = StructuredQuery(dataset="normalized", limit=2,
                         column_filters={"severity": "critical"})
    rows = asyncio.run(execute_structured_query(sq))
    # 3 match 'critical', truncated to limit=2
    assert len(rows) == 2
    assert all(r["severity"] == "critical" for r in rows)


def test_execute_normalized_ignores_absent_filter_column(monkeypatch):
    """A filter column not present in any normalized row is ignored rather than
    emptying the result set."""
    async def _fake_query_normalized(**kwargs):
        return [{"indicator": "a"}, {"indicator": "b"}]

    monkeypatch.setattr(nl, "query_normalized", _fake_query_normalized)
    sq = StructuredQuery(dataset="normalized", limit=10,
                         column_filters={"severity": "critical"})
    rows = asyncio.run(execute_structured_query(sq))
    assert len(rows) == 2  # severity absent → filter skipped
