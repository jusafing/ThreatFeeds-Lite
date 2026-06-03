"""Tests for GET /api/viewer/field-presence (issue_local_009, review_01).

The endpoint derives the Raw-table default columns on demand from the most
recent entries, so these tests seed entries via insert_entry and assert the
endpoint surfaces their populated fields.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.db.manager import insert_entry


@pytest.fixture
def temp_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.db.manager.DATA_DIR", tmp_path)
    return tmp_path


def test_field_presence_empty(temp_data_dir):
    with TestClient(app) as client:
        resp = client.get("/api/viewer/field-presence")
    assert resp.status_code == 200
    assert resp.json() == {"fields": []}


@pytest.mark.asyncio
async def test_field_presence_derived_from_recent_entries(temp_data_dir):
    await insert_entry("fp_src", {
        "source": "fp_src", "indicator": "1.1.1.1", "indicator_type": "ip",
        "cve_id": "CVE-2026-1", "title": "", "published_at": "2026-01-01",
    })

    with TestClient(app) as client:
        resp = client.get("/api/viewer/field-presence")
    assert resp.status_code == 200
    fields = resp.json()["fields"]
    assert "cve_id" in fields
    assert "indicator" in fields
    assert "indicator_type" in fields
    # Empty / internal / always-shown columns excluded.
    assert "title" not in fields
    assert "source" not in fields
    assert "ingested_at" not in fields
