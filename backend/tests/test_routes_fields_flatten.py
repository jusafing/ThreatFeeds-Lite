"""Tests for /api/fields/flatten-depth (prompts-015)."""
from __future__ import annotations

import pytest
import yaml
from fastapi.testclient import TestClient

from backend.main import app
from backend.config import loader


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Redirect FIELDS_PATH to a tmp file seeded with the current defaults."""
    fake = tmp_path / "feed-fields.yaml"
    fake.write_text(
        yaml.safe_dump({
            "ingest_all_fields": True,
            "flatten_max_depth": 5,
            "core_fields": [],
            "custom_fields": [],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(loader, "FIELDS_PATH", fake)
    return TestClient(app)


def test_get_flatten_depth_default(client):
    resp = client.get("/api/fields/flatten-depth")
    assert resp.status_code == 200
    assert resp.json() == {"flatten_max_depth": 5}


def test_put_flatten_depth_round_trip(client):
    resp = client.put("/api/fields/flatten-depth", json={"flatten_max_depth": 7})
    assert resp.status_code == 200
    assert resp.json() == {"flatten_max_depth": 7}
    # Re-read to confirm persisted
    resp2 = client.get("/api/fields/flatten-depth")
    assert resp2.json() == {"flatten_max_depth": 7}


def test_put_flatten_depth_rejects_out_of_range(client):
    resp = client.put("/api/fields/flatten-depth", json={"flatten_max_depth": 99})
    assert resp.status_code == 400


def test_put_flatten_depth_rejects_non_integer(client):
    resp = client.put("/api/fields/flatten-depth", json={"flatten_max_depth": "five"})
    assert resp.status_code == 400


def test_put_flatten_depth_rejects_bool(client):
    """Booleans are int subclasses in Python — must be rejected explicitly."""
    resp = client.put("/api/fields/flatten-depth", json={"flatten_max_depth": True})
    assert resp.status_code == 400
