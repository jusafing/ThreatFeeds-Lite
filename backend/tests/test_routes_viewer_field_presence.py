"""Tests for GET /api/viewer/field-presence (issue_local_009)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.db import meta as meta_mod


@pytest.fixture
def isolated_meta(tmp_path, monkeypatch):
    fake_path = tmp_path / "meta.db"
    monkeypatch.setattr(meta_mod, "META_DB", fake_path)
    monkeypatch.setattr(meta_mod, "DATA_DIR", tmp_path)
    return fake_path


def test_field_presence_empty(isolated_meta):
    with TestClient(app) as client:
        resp = client.get("/api/viewer/field-presence")
    assert resp.status_code == 200
    assert resp.json() == {"fields": []}


@pytest.mark.asyncio
async def test_field_presence_returns_ranked_fields(isolated_meta):
    from datetime import datetime, timezone

    await meta_mod.record_field_presence(
        {"cve_id": 5, "actor": 2}, when=datetime(2025, 1, 1, tzinfo=timezone.utc)
    )
    await meta_mod.record_field_presence(
        {"actor": 1}, when=datetime(2025, 1, 2, tzinfo=timezone.utc)
    )

    with TestClient(app) as client:
        resp = client.get("/api/viewer/field-presence")
    assert resp.status_code == 200
    fields = resp.json()["fields"]
    # Most-recently populated first.
    assert fields[0] == "actor"
    assert set(fields) == {"actor", "cve_id"}
