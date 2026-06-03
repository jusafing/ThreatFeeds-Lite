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


def test_create_without_conditions_returns_400(client):
    r = client.post("/api/watchers", json=_payload(conditions=[]))
    assert r.status_code == 400
    assert "condition" in r.json()["detail"].lower()


def test_create_numeric_condition_non_numeric_value_returns_400(client):
    r = client.post(
        "/api/watchers",
        json=_payload(conditions=[{"field": "confidence", "value": "high", "match_type": "gte"}]),
    )
    assert r.status_code == 400


def test_list_includes_last_triggered_at(client):
    client.post("/api/watchers", json=_payload())
    # Before any trigger it is null.
    body = client.get("/api/watchers").json()
    assert body[0]["last_triggered_at"] is None

    import anyio

    async def _seed():
        await store.record_triggers(
            "critical-cves",
            [{"dataset": "normalized", "source_entry_id": 1, "source_name": "a", "event": {"id": 1}}],
            max_events=100,
        )

    anyio.run(_seed)
    body = client.get("/api/watchers").json()
    assert body[0]["last_triggered_at"] is not None


def test_meta_fields_endpoint(client):
    r = client.get("/api/watchers/meta/fields", params={"dataset": "raw"})
    assert r.status_code == 200
    fields = r.json()["fields"]
    assert "severity" in fields and "cve_id" in fields


def test_meta_fields_feed_aware_samples_custom_fields(client, monkeypatch):
    # Sampling raw entries for the selected feed surfaces an extra-JSON custom
    # field that is not part of the static schema.
    async def _fake_entries(source_name=None, limit=500, **kw):
        if source_name == "feed-a":
            return [{"id": 1, "indicator": "1.2.3.4", "custom_threat_score": "9"}]
        return []

    async def _fake_norm(source_name=None, limit=500, **kw):
        return []

    monkeypatch.setattr(routes_watchers, "query_entries", _fake_entries)
    monkeypatch.setattr(routes_watchers, "query_normalized", _fake_norm)

    r = client.get(
        "/api/watchers/meta/fields", params={"dataset": "raw", "feeds": ["feed-a"]}
    )
    assert r.status_code == 200
    fields = r.json()["fields"]
    assert "custom_threat_score" in fields
    assert "indicator" in fields
    # Internal/system keys are never offered.
    assert "extra" not in fields and "raw" not in fields


def test_meta_fields_feed_aware_unions_multiple_feeds(client, monkeypatch):
    async def _fake_entries(source_name=None, limit=500, **kw):
        return {
            "feed-a": [{"id": 1, "field_a": "x"}],
            "feed-b": [{"id": 2, "field_b": "y"}],
        }.get(source_name, [])

    async def _fake_norm(source_name=None, limit=500, **kw):
        return []

    monkeypatch.setattr(routes_watchers, "query_entries", _fake_entries)
    monkeypatch.setattr(routes_watchers, "query_normalized", _fake_norm)

    r = client.get(
        "/api/watchers/meta/fields",
        params={"dataset": "raw", "feeds": ["feed-a", "feed-b"]},
    )
    fields = r.json()["fields"]
    assert "field_a" in fields and "field_b" in fields


def test_meta_fields_no_feed_samples_union_of_all_sources(client, monkeypatch):
    # review_02b: with NO feed selected, every source is sampled so custom
    # fields from any feed appear (not just the static schema subset).
    async def _fake_entries(source_name=None, limit=500, **kw):
        return {
            "feed-a": [{"id": 1, "custom_a": "x"}],
            "feed-b": [{"id": 2, "custom_b": "y"}],
        }.get(source_name, [])

    async def _fake_norm(source_name=None, limit=500, **kw):
        return []

    async def _fake_summary():
        return []

    monkeypatch.setattr(routes_watchers, "_get_all_sources", lambda: ["feed-a", "feed-b"])
    monkeypatch.setattr(routes_watchers, "query_entries", _fake_entries)
    monkeypatch.setattr(routes_watchers, "query_normalized", _fake_norm)
    monkeypatch.setattr(routes_watchers, "get_normalized_summary", _fake_summary)

    r = client.get("/api/watchers/meta/fields", params={"dataset": "raw"})
    assert r.status_code == 200
    fields = r.json()["fields"]
    # Custom fields from BOTH sources surface even with no feed filter.
    assert "custom_a" in fields and "custom_b" in fields
    assert "extra" not in fields and "raw" not in fields


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


def test_public_feed_serves_beyond_max_feed_events_until_cleanup(client):
    """issue_local_008: the live feed renders up to the global hard limit, not
    the per-watcher max_feed_events; the periodic cleanup trims it back."""
    import anyio

    async def _seed():
        await store.create_watcher(_payload(format="json", max_feed_events=1))
        await store.record_triggers(
            "critical-cves",
            [
                {"dataset": "normalized", "source_entry_id": i, "source_name": "feed-a",
                 "event": {"id": i, "cve_id": f"CVE-2024-{i}"}}
                for i in range(1, 4)
            ],
            max_events=1000,
        )

    anyio.run(_seed)
    # All 3 are visible despite max_feed_events=1.
    assert client.get("/feed/watcher/critical-cves/").json()["count"] == 3

    # After a cleanup pass the feed is trimmed to max_feed_events (1 newest).
    async def _cleanup():
        await store.run_watcher_cleanup("critical-cves")

    anyio.run(_cleanup)
    body = client.get("/feed/watcher/critical-cves/").json()
    assert body["count"] == 1
    assert body["events"][0]["cve_id"] == "CVE-2024-3"


def test_feed_is_public_but_admin_routes_gated_when_auth_enabled(client, monkeypatch):
    # Turn auth ON for the middleware (no session cookie supplied).
    monkeypatch.setattr(main_mod, "load_auth_enabled", lambda: True)
    # Management API requires a session -> 401.
    assert client.get("/api/watchers").status_code == 401
    # Public feed stays reachable (lives outside /api/).
    _seed_events("json")
    assert client.get("/feed/watcher/critical-cves/").status_code == 200


# ── Manual trigger (issue_local_007) ────────────────────────────────────────


def test_trigger_missing_watcher_returns_404(client):
    assert client.post("/api/watchers/nope/trigger").status_code == 404


def test_trigger_local_watcher_evaluates_without_delivery(client, monkeypatch):
    client.post("/api/watchers", json=_payload(publish_target="local"))

    async def fake_eval(watcher, datasets, *, ignore_enabled=False):
        assert ignore_enabled is True
        assert datasets == {"raw", "normalized"}
        return 4

    called = {"delivered": False}

    async def fake_deliver(watcher):  # pragma: no cover - must NOT run for local
        called["delivered"] = True
        return {"delivered": 0, "failed": 0}

    monkeypatch.setattr(routes_watchers.engine, "evaluate_watcher", fake_eval)
    monkeypatch.setattr(routes_watchers.delivery, "deliver_pending", fake_deliver)

    r = client.post("/api/watchers/critical-cves/trigger")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["triggered"] == 4
    assert body["delivery"] == {"delivered": 0, "failed": 0}
    assert called["delivered"] is False


def test_create_webhook_format_is_persisted_and_returned(client):
    r = client.post(
        "/api/watchers",
        json=_payload(
            publish_target="webhook",
            webhook_url="https://discord.com/api/webhooks/1/abc",
            webhook_format="discord",
        ),
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["webhook_format"] == "discord"
    assert body["last_delivery_detail"] is None
    got = client.get("/api/watchers/critical-cves").json()
    assert got["webhook_format"] == "discord"


def test_create_rejects_invalid_webhook_format(client):
    r = client.post(
        "/api/watchers",
        json=_payload(
            publish_target="webhook",
            webhook_url="https://x.example/in",
            webhook_format="carrier-pigeon",
        ),
    )
    assert r.status_code == 400


def test_trigger_remote_watcher_delivers(client, monkeypatch):
    client.post(
        "/api/watchers",
        json=_payload(publish_target="webhook", webhook_url="https://x.example/in"),
    )

    async def fake_eval(watcher, datasets, *, ignore_enabled=False):
        return 2

    async def fake_deliver(watcher):
        return {"delivered": 2, "failed": 0}

    monkeypatch.setattr(routes_watchers.engine, "evaluate_watcher", fake_eval)
    monkeypatch.setattr(routes_watchers.delivery, "deliver_pending", fake_deliver)

    r = client.post("/api/watchers/critical-cves/trigger")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["triggered"] == 2
    assert body["delivery"] == {"delivered": 2, "failed": 0}
