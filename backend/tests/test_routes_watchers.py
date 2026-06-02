"""Tests for watcher routes + public feed (issue_local_006).

Uses TestClient WITHOUT a context manager so the app lifespan (scheduler /
real-data init) does not run; the watcher store initializes itself lazily and is
redirected to a tmp DB. Covers CRUD via the API, the public feed in all three
formats, public reachability (no /api auth), and admin-gating when auth is on.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.db import watchers as store
from backend.api import routes_watchers
import backend.main as main_mod


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "_WATCHERS_DB_PATH", tmp_path / "watchers.db")
    # Neutralize scheduler reload side-effects triggered by mutating routes.
    monkeypatch.setattr(routes_watchers, "_reschedule", lambda: None)
    return TestClient(app)


def _payload(**over):
    base = {
        "name": "Critical CVEs",
        "severity": "critical",
        "dataset": "normalized",
        "feeds": [],
        "conditions": [{"field": "cve_id", "value": "CVE-2024-*", "match_type": "wildcard"}],
        "mode": "realtime",
        "interval_sec": 120,
        "format": "json",
        "max_feed_events": 5,
        "enabled": True,
    }
    base.update(over)
    return base


def test_create_list_get_update_toggle_delete(client):
    # Create
    r = client.post("/api/watchers", json=_payload())
    assert r.status_code == 201, r.text
    w = r.json()
    assert w["id"] == "critical-cves"

    # List
    r = client.get("/api/watchers")
    assert r.status_code == 200
    assert any(x["id"] == "critical-cves" for x in r.json())

    # Get
    assert client.get("/api/watchers/critical-cves").status_code == 200
    assert client.get("/api/watchers/missing").status_code == 404

    # Update
    r = client.put("/api/watchers/critical-cves", json=_payload(severity="high"))
    assert r.status_code == 200
    assert r.json()["severity"] == "high"

    # Toggle
    r = client.put("/api/watchers/critical-cves/enabled", json={"enabled": False})
    assert r.status_code == 200
    assert r.json()["enabled"] is False

    # Delete
    assert client.delete("/api/watchers/critical-cves").status_code == 204
    assert client.get("/api/watchers/critical-cves").status_code == 404


def test_create_invalid_returns_400(client):
    r = client.post("/api/watchers", json=_payload(severity="bogus"))
    assert r.status_code == 400


def test_create_invalid_regex_returns_400(client):
    r = client.post(
        "/api/watchers",
        json=_payload(conditions=[{"field": "title", "value": "(", "match_type": "regex"}]),
    )
    assert r.status_code == 400


def test_meta_fields_endpoint(client):
    r = client.get("/api/watchers/meta/fields", params={"dataset": "raw"})
    assert r.status_code == 200
    fields = r.json()["fields"]
    assert "severity" in fields and "cve_id" in fields


def test_events_endpoint_returns_triggers(client):
    client.post("/api/watchers", json=_payload())
    # Seed triggered events directly through the store.
    import anyio

    async def _seed():
        await store.record_triggers(
            "critical-cves",
            [{"dataset": "normalized", "source_entry_id": 1, "source_name": "a",
              "event": {"id": 1, "cve_id": "CVE-2024-1"}}],
            max_events=100,
        )

    anyio.run(_seed)
    r = client.get("/api/watchers/critical-cves/events")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["events"][0]["event"]["cve_id"] == "CVE-2024-1"


# ── Public feed ─────────────────────────────────────────────────────────────

def _seed_events(fmt: str):
    import anyio

    async def _go():
        await store.create_watcher(_payload(format=fmt, max_feed_events=10))
        await store.record_triggers(
            "critical-cves",
            [
                {"dataset": "normalized", "source_entry_id": 1, "source_name": "feed-a",
                 "event": {"id": 1, "cve_id": "CVE-2024-1", "indicator": "1.2.3.4"}},
                {"dataset": "normalized", "source_entry_id": 2, "source_name": "feed-a",
                 "event": {"id": 2, "cve_id": "CVE-2024-2", "indicator": "5.6.7.8"}},
            ],
            max_events=100,
        )

    anyio.run(_go)


def test_public_feed_json(client):
    _seed_events("json")
    r = client.get("/feed/watcher/critical-cves/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    body = r.json()
    assert body["watcher"] == "Critical CVEs"
    assert body["count"] == 2
    assert body["events"][0]["triggered_at"]
    assert body["events"][0]["watcher"] == "Critical CVEs"


def test_public_feed_csv(client):
    _seed_events("csv")
    r = client.get("/feed/watcher/critical-cves/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    text = r.text
    assert "triggered_at,watcher,source_name" in text.splitlines()[0]
    assert "CVE-2024-1" in text


def test_public_feed_xml(client):
    _seed_events("xml")
    r = client.get("/feed/watcher/critical-cves/")
    assert r.status_code == 200
    assert "xml" in r.headers["content-type"]
    assert "<rss" in r.text and "<item>" in r.text


def test_public_feed_missing_watcher_404(client):
    assert client.get("/feed/watcher/nope/").status_code == 404


def test_public_feed_empty_is_ok(client):
    client.post("/api/watchers", json=_payload(format="json"))
    r = client.get("/feed/watcher/critical-cves/")
    assert r.status_code == 200
    assert r.json()["count"] == 0


def test_feed_is_public_but_admin_routes_gated_when_auth_enabled(client, monkeypatch):
    # Turn auth ON for the middleware (no session cookie supplied).
    monkeypatch.setattr(main_mod, "load_auth_enabled", lambda: True)
    # Management API requires a session -> 401.
    assert client.get("/api/watchers").status_code == 401
    # Public feed stays reachable (lives outside /api/).
    _seed_events("json")
    assert client.get("/feed/watcher/critical-cves/").status_code == 200
