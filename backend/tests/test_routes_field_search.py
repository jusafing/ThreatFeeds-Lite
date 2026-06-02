"""Integration tests for exact-column field search across the raw and
normalized stores (issue_local_02).

These drive the real HTTP routes against isolated tmp databases (no LLM, no
stubbing of the query layer) to prove that ``?field=name=value``:

- filters each store on an exact column match,
- queries the raw and normalized stores independently (the same field name
  returns store-specific rows), and
- silently drops unknown / injection-shaped column names instead of letting
  them reach SQL.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

import backend.db.manager as mgr
import backend.normalizer.db as ndb
from backend.main import app


@pytest.fixture
def field_env(tmp_path, monkeypatch):
    """Isolate both stores in tmp and seed raw + normalized rows.

    Raw and normalized are seeded with deliberately *different* indicators for
    the same ``severity=critical`` filter so a test can prove the two endpoints
    hit different stores.
    """
    monkeypatch.setattr(mgr, "DATA_DIR", tmp_path)
    monkeypatch.setattr(ndb, "_NORM_DB_PATH", tmp_path / "normalized.db")

    async def _seed():
        # Raw store: one critical, one low.
        await mgr.insert_entry("feed-raw", {
            "source": "feed-raw", "indicator": "10.0.0.1",
            "indicator_type": "ipv4", "severity": "critical",
            "ingest_mode": "push",
        })
        await mgr.insert_entry("feed-raw", {
            "source": "feed-raw", "indicator": "10.0.0.2",
            "indicator_type": "ipv4", "severity": "low",
            "ingest_mode": "push",
        })
        # Normalized store: different indicators, same severity values.
        await ndb.insert_normalized({
            "source_entry_id": 1, "source_name": "feed-norm",
            "indicator": "192.168.1.1", "indicator_type": "ipv4",
            "severity": "critical",
        })
        await ndb.insert_normalized({
            "source_entry_id": 2, "source_name": "feed-norm",
            "indicator": "192.168.1.2", "indicator_type": "ipv4",
            "severity": "low",
        })

    asyncio.run(_seed())
    yield


def test_raw_field_search_filters_exact_column(field_env):
    with TestClient(app) as client:
        resp = client.get("/api/viewer/entries", params={"field": "severity=critical"})
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert [r["indicator"] for r in rows] == ["10.0.0.1"]


def test_normalized_field_search_filters_exact_column(field_env):
    with TestClient(app) as client:
        resp = client.get(
            "/api/normalizer/entries", params={"field": "severity=critical"},
        )
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert [r["indicator"] for r in rows] == ["192.168.1.1"]


def test_raw_and_normalized_field_search_hit_independent_stores(field_env):
    """The same field filter returns the raw store's rows from the viewer route
    and the normalized store's rows from the normalizer route."""
    with TestClient(app) as client:
        raw = client.get(
            "/api/viewer/entries", params={"field": "severity=critical"},
        ).json()
        norm = client.get(
            "/api/normalizer/entries", params={"field": "severity=critical"},
        ).json()

    raw_indicators = {r["indicator"] for r in raw}
    norm_indicators = {r["indicator"] for r in norm}
    assert raw_indicators == {"10.0.0.1"}
    assert norm_indicators == {"192.168.1.1"}
    assert raw_indicators.isdisjoint(norm_indicators)


def test_unknown_and_injection_field_names_are_dropped(field_env):
    """Unknown columns and injection-shaped names never reach SQL: the filter is
    dropped and all rows for the store are returned."""
    params = [
        ("field", "not_a_column=x"),
        ("field", "1; DROP TABLE entries; --=y"),
    ]
    with TestClient(app) as client:
        raw = client.get("/api/viewer/entries", params=params)
        norm = client.get("/api/normalizer/entries", params=params)
    assert raw.status_code == 200, raw.text
    assert norm.status_code == 200, norm.text
    assert len(raw.json()) == 2
    assert len(norm.json()) == 2
