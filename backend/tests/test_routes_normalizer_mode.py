"""Tests for normalizer-config mode validation (prompts-032 Phase E).

The PUT /api/normalizer/config route accepts only the three valid modes
(auto / manual / smart) and rejects anything else with 400. Isolated via a
tmp_path-pointed normalizer-config.yaml.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.normalizer import config as norm_cfg_mod


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(
        norm_cfg_mod, "_NORMALIZER_CONFIG_PATH", tmp_path / "normalizer-config.yaml",
    )
    yield


@pytest.mark.parametrize("mode", ["auto", "manual", "smart"])
def test_put_config_accepts_valid_modes(mode):
    client = TestClient(app)
    r = client.put("/api/normalizer/config", json={"mode": mode})
    assert r.status_code == 200
    assert r.json()["mode"] == mode


def test_put_config_rejects_invalid_mode():
    client = TestClient(app)
    r = client.put("/api/normalizer/config", json={"mode": "bogus"})
    assert r.status_code == 400
    assert "bogus" in r.json()["detail"]


def test_put_config_without_mode_key_is_unvalidated():
    """Bodies omitting ``mode`` merge normally (no mode validation triggered)."""
    client = TestClient(app)
    r = client.put("/api/normalizer/config", json={"interval_minutes": 42})
    assert r.status_code == 200
    assert r.json()["interval_minutes"] == 42
