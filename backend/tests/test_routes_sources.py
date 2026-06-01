"""Tests for source CRUD routes — focus on auto-ingest on add."""
from __future__ import annotations

import asyncio

import pytest


@pytest.mark.asyncio
async def test_add_api_pull_triggers_auto_ingest(tmp_path, monkeypatch):
    """POST with auto_ingest=true spawns a background ingest when enabled."""
    import backend.api.routes_sources as rs

    # Stub YAML I/O
    yaml_state: dict = {}
    monkeypatch.setattr(rs, "load_sources", lambda: yaml_state)
    monkeypatch.setattr(rs, "save_sources", lambda d: yaml_state.update(d))

    # Capture calls to pull_api_source
    calls: list = []

    async def fake_pull(src):
        calls.append(src["name"])
        return {"inserted": 0, "skipped": 0, "errors": []}

    monkeypatch.setattr(rs, "pull_api_source", fake_pull)

    src = {"name": "auto_src", "url": "http://x", "enabled": True, "auto_ingest": True}
    result = await rs.add_api_pull(src)
    assert result["name"] == "auto_src"

    # Let the background task finish
    await asyncio.sleep(0.05)
    assert calls == ["auto_src"]


@pytest.mark.asyncio
async def test_add_api_pull_default_does_not_auto_ingest(tmp_path, monkeypatch):
    """Default (no auto_ingest flag) must NOT trigger an ingest — UI uses preview/confirm."""
    import backend.api.routes_sources as rs

    yaml_state: dict = {}
    monkeypatch.setattr(rs, "load_sources", lambda: yaml_state)
    monkeypatch.setattr(rs, "save_sources", lambda d: yaml_state.update(d))

    calls: list = []

    async def fake_pull(src):
        calls.append(src["name"])
        return {"inserted": 0, "skipped": 0, "errors": []}

    monkeypatch.setattr(rs, "pull_api_source", fake_pull)

    src = {"name": "default_src", "url": "http://x", "enabled": True}
    await rs.add_api_pull(src)
    await asyncio.sleep(0.05)
    assert calls == []


@pytest.mark.asyncio
async def test_add_api_pull_skips_when_auto_ingest_false(tmp_path, monkeypatch):
    """auto_ingest=False suppresses the immediate ingest."""
    import backend.api.routes_sources as rs

    yaml_state: dict = {}
    monkeypatch.setattr(rs, "load_sources", lambda: yaml_state)
    monkeypatch.setattr(rs, "save_sources", lambda d: yaml_state.update(d))

    calls: list = []

    async def fake_pull(src):
        calls.append(src["name"])
        return {"inserted": 0, "skipped": 0, "errors": []}

    monkeypatch.setattr(rs, "pull_api_source", fake_pull)

    src = {"name": "noauto", "url": "http://x", "enabled": True, "auto_ingest": False}
    await rs.add_api_pull(src)
    await asyncio.sleep(0.05)
    assert calls == []


@pytest.mark.asyncio
async def test_add_api_pull_skips_when_disabled(tmp_path, monkeypatch):
    """enabled=False suppresses the immediate ingest."""
    import backend.api.routes_sources as rs

    yaml_state: dict = {}
    monkeypatch.setattr(rs, "load_sources", lambda: yaml_state)
    monkeypatch.setattr(rs, "save_sources", lambda d: yaml_state.update(d))

    calls: list = []

    async def fake_pull(src):
        calls.append(src["name"])
        return {"inserted": 0, "skipped": 0, "errors": []}

    monkeypatch.setattr(rs, "pull_api_source", fake_pull)

    src = {"name": "disabled", "url": "http://x", "enabled": False}
    await rs.add_api_pull(src)
    await asyncio.sleep(0.05)
    assert calls == []


# ---------------------------------------------------------------------------
# prompts-013 item 3 — preview / confirm / cancel flow for pull sources
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_preview_returns_sample(tmp_path, monkeypatch):
    """POST /api/sources/preview/api-pull returns sample of normalised entries
    without persisting the source to YAML."""
    import backend.api.routes_sources as rs
    import backend.ingestion.source_preview as sp

    yaml_state: dict = {}
    monkeypatch.setattr(rs, "load_sources", lambda: yaml_state)
    monkeypatch.setattr(rs, "save_sources", lambda d: yaml_state.update(d))

    async def fake_fetch(source):
        return "json", [{"indicator": "1.1.1.1"}, {"indicator": "2.2.2.2"}]

    monkeypatch.setitem(sp._FETCHERS, "api_pull", fake_fetch)
    # Bypass normaliser side-effects (loader IO)
    monkeypatch.setattr(sp, "normalise", lambda raw, **kw: {**raw, "source": kw["source_name"]})

    src = {"name": "prev_src", "url": "http://x", "enabled": True}
    result = await rs.preview_pull_source("api-pull", src)

    assert result["source_name"] == "prev_src"
    assert result["total"] == 2
    assert len(result["sample"]) == 2
    assert result["preview_id"]
    # Source NOT persisted yet
    assert yaml_state.get("api_pull", []) == []


@pytest.mark.asyncio
async def test_confirm_preview_persists_and_ingests(tmp_path, monkeypatch):
    """POST /api/sources/preview/confirm/{id} writes source to YAML and inserts entries."""
    import backend.api.routes_sources as rs
    import backend.ingestion.source_preview as sp

    yaml_state: dict = {}
    monkeypatch.setattr(rs, "load_sources", lambda: yaml_state)
    monkeypatch.setattr(rs, "save_sources", lambda d: yaml_state.update(d))
    monkeypatch.setattr(sp, "load_sources", lambda: yaml_state)
    monkeypatch.setattr(sp, "save_sources", lambda d: yaml_state.update(d))

    async def fake_fetch(source):
        return "json", [{"indicator": "x"}, {"indicator": "y"}]

    monkeypatch.setitem(sp._FETCHERS, "api_pull", fake_fetch)
    monkeypatch.setattr(sp, "normalise", lambda raw, **kw: {**raw, "source": kw["source_name"]})

    inserted_calls: list = []

    async def fake_insert(name, entry):
        inserted_calls.append((name, entry.get("indicator")))
        return "inserted"

    monkeypatch.setattr(sp, "insert_entry", fake_insert)

    src = {"name": "conf_src", "url": "http://x", "enabled": True}
    preview = await rs.preview_pull_source("api-pull", src)

    from fastapi import BackgroundTasks
    result = await rs.confirm_preview_source(preview["preview_id"], BackgroundTasks())
    assert result["inserted"] == 2
    assert result["total_read"] == 2
    assert yaml_state.get("api_pull")[0]["name"] == "conf_src"
    assert inserted_calls == [("conf_src", "x"), ("conf_src", "y")]


@pytest.mark.asyncio
async def test_cancel_preview_evicts(tmp_path, monkeypatch):
    """POST /api/sources/preview/cancel/{id} removes the preview without persisting."""
    import backend.api.routes_sources as rs
    import backend.ingestion.source_preview as sp

    yaml_state: dict = {}
    monkeypatch.setattr(rs, "load_sources", lambda: yaml_state)
    monkeypatch.setattr(rs, "save_sources", lambda d: yaml_state.update(d))

    async def fake_fetch(source):
        return "json", [{"indicator": "z"}]

    monkeypatch.setitem(sp._FETCHERS, "api_pull", fake_fetch)
    monkeypatch.setattr(sp, "normalise", lambda raw, **kw: {**raw, "source": kw["source_name"]})

    src = {"name": "cancel_src", "url": "http://x", "enabled": True}
    preview = await rs.preview_pull_source("api-pull", src)
    pid = preview["preview_id"]

    cancel_res = await rs.cancel_preview_source(pid)
    assert cancel_res["cancelled"] is True

    # Confirming a cancelled preview should 404
    from fastapi import BackgroundTasks, HTTPException
    with pytest.raises(HTTPException) as exc:
        await rs.confirm_preview_source(pid, BackgroundTasks())
    assert exc.value.status_code == 404

    # Source still not persisted
    assert yaml_state.get("api_pull", []) == []


# ── Threat-intel catalogue (prompts-042) ──────────────────────────────────────

_FAKE_CATALOG = [
    {
        "name": "cisa_kev",
        "title": "CISA KEV",
        "kind": "remote_json_pull",
        "url": "https://example.com/kev.json",
        "info": "actively exploited CVEs",
        "default_interval_minutes": 360,
    },
    {
        "name": "cisa_advisories",
        "title": "CISA Advisories",
        "kind": "rss_pull",
        "url": "https://example.com/all.xml",
        "info": "advisories rss",
        "default_interval_minutes": 60,
    },
]


@pytest.mark.asyncio
async def test_threat_intel_catalog_reports_disabled_by_default(monkeypatch):
    """With nothing in sources.yaml, every catalogue feed reports enabled=False."""
    import backend.api.routes_sources as rs

    yaml_state: dict = {}
    monkeypatch.setattr(rs, "load_default_sources", lambda: _FAKE_CATALOG)
    monkeypatch.setattr(rs, "load_sources", lambda: yaml_state)
    monkeypatch.setattr(rs, "save_sources", lambda d: yaml_state.update(d))

    view = await rs.get_threat_intel_catalog()
    assert {v["name"] for v in view} == {"cisa_kev", "cisa_advisories"}
    assert all(v["enabled"] is False for v in view)
    kev = next(v for v in view if v["name"] == "cisa_kev")
    assert kev["interval_minutes"] == 360  # falls back to default


@pytest.mark.asyncio
async def test_threat_intel_save_enables_writes_bucket(monkeypatch):
    """Enabling a feed writes an entry into the matching sources.yaml bucket."""
    import backend.api.routes_sources as rs

    yaml_state: dict = {}
    monkeypatch.setattr(rs, "load_default_sources", lambda: _FAKE_CATALOG)
    monkeypatch.setattr(rs, "load_sources", lambda: yaml_state)
    monkeypatch.setattr(rs, "save_sources", lambda d: yaml_state.update(d))

    body = [
        rs.ThreatIntelToggle(name="cisa_kev", enabled=True, continuous=True, interval_minutes=120),
        rs.ThreatIntelToggle(name="cisa_advisories", enabled=False),
    ]
    view = await rs.save_threat_intel_sources(body)

    bucket = yaml_state["remote_json_pull"]
    assert len(bucket) == 1
    entry = bucket[0]
    assert entry["name"] == "cisa_kev"
    assert entry["enabled"] is True
    assert entry["continuous"] is True
    assert entry["interval_minutes"] == 120
    assert entry["url"] == "https://example.com/kev.json"
    assert entry["source_origin"] == "threat_intel_catalog"
    # rss feed left disabled -> no bucket entry
    assert yaml_state.get("rss_pull", []) == []
    # returned view reflects enabled state
    kev = next(v for v in view if v["name"] == "cisa_kev")
    assert kev["enabled"] is True and kev["continuous"] is True


@pytest.mark.asyncio
async def test_threat_intel_save_disable_removes_entry(monkeypatch):
    """Disabling a previously-enabled feed removes its bucket entry."""
    import backend.api.routes_sources as rs

    yaml_state: dict = {
        "remote_json_pull": [
            {"name": "cisa_kev", "enabled": True, "url": "https://example.com/kev.json",
             "continuous": True, "interval_minutes": 120, "source_origin": "threat_intel_catalog"}
        ]
    }
    monkeypatch.setattr(rs, "load_default_sources", lambda: _FAKE_CATALOG)
    monkeypatch.setattr(rs, "load_sources", lambda: yaml_state)
    monkeypatch.setattr(rs, "save_sources", lambda d: yaml_state.update(d))

    body = [rs.ThreatIntelToggle(name="cisa_kev", enabled=False)]
    await rs.save_threat_intel_sources(body)
    assert yaml_state.get("remote_json_pull", []) == []


@pytest.mark.asyncio
async def test_threat_intel_save_invalid_interval_uses_default(monkeypatch):
    """An invalid/missing interval falls back to the catalogue default."""
    import backend.api.routes_sources as rs

    yaml_state: dict = {}
    monkeypatch.setattr(rs, "load_default_sources", lambda: _FAKE_CATALOG)
    monkeypatch.setattr(rs, "load_sources", lambda: yaml_state)
    monkeypatch.setattr(rs, "save_sources", lambda d: yaml_state.update(d))

    body = [rs.ThreatIntelToggle(name="cisa_advisories", enabled=True, interval_minutes=0)]
    await rs.save_threat_intel_sources(body)
    entry = yaml_state["rss_pull"][0]
    assert entry["interval_minutes"] == 60  # default for cisa_advisories


@pytest.mark.asyncio
async def test_threat_intel_save_unknown_name_404(monkeypatch):
    """Saving an unknown catalogue name raises 404."""
    import backend.api.routes_sources as rs
    from fastapi import HTTPException

    yaml_state: dict = {}
    monkeypatch.setattr(rs, "load_default_sources", lambda: _FAKE_CATALOG)
    monkeypatch.setattr(rs, "load_sources", lambda: yaml_state)
    monkeypatch.setattr(rs, "save_sources", lambda d: yaml_state.update(d))

    with pytest.raises(HTTPException) as exc:
        await rs.save_threat_intel_sources([rs.ThreatIntelToggle(name="nope", enabled=True)])
    assert exc.value.status_code == 404


# ── Secret redaction (prompts-045 security audit, BLOCKER #1) ──────────────────

@pytest.mark.asyncio
async def test_list_api_pull_redacts_header_values(monkeypatch):
    """GET must never return raw per-source request header secrets."""
    import backend.api.routes_sources as rs

    yaml_state = {
        "api_pull": [
            {
                "name": "secret_src",
                "url": "http://x",
                "enabled": True,
                "headers": {"Authorization": "Bearer SUPERSECRET", "X-Api-Key": "abc123"},
            }
        ]
    }
    monkeypatch.setattr(rs, "load_sources", lambda: yaml_state)

    result = await rs.list_api_pull()
    headers = result[0]["headers"]
    assert headers == {"Authorization": rs._REDACTED, "X-Api-Key": rs._REDACTED}
    # The plaintext secret must not appear anywhere in the response.
    assert "SUPERSECRET" not in str(result)
    assert "abc123" not in str(result)
    # Stored config is untouched (redaction is on the copy only).
    assert yaml_state["api_pull"][0]["headers"]["Authorization"] == "Bearer SUPERSECRET"


@pytest.mark.asyncio
async def test_update_api_pull_restores_masked_headers(monkeypatch):
    """Re-PUTing a masked source (as the editor does) must preserve the secret."""
    import backend.api.routes_sources as rs

    yaml_state = {
        "api_pull": [
            {
                "name": "src",
                "url": "http://old",
                "enabled": True,
                "headers": {"Authorization": "Bearer KEEPME"},
            }
        ]
    }
    monkeypatch.setattr(rs, "load_sources", lambda: yaml_state)
    monkeypatch.setattr(rs, "save_sources", lambda d: yaml_state.update(d))

    # Simulate the UI toggle: it re-sends the redacted object with a changed field.
    masked = {
        "name": "src",
        "url": "http://new",
        "enabled": False,
        "headers": {"Authorization": rs._REDACTED},
    }
    out = await rs.update_api_pull("src", masked)

    # Stored secret preserved, other fields updated.
    stored = yaml_state["api_pull"][0]
    assert stored["headers"]["Authorization"] == "Bearer KEEPME"
    assert stored["url"] == "http://new"
    assert stored["enabled"] is False
    # Response is itself redacted.
    assert out["headers"]["Authorization"] == rs._REDACTED


@pytest.mark.asyncio
async def test_update_api_pull_rotates_new_header_value(monkeypatch):
    """A non-masked header value is a deliberate rotation and must be saved."""
    import backend.api.routes_sources as rs

    yaml_state = {
        "api_pull": [
            {"name": "src", "url": "http://x", "headers": {"Authorization": "Bearer OLD"}}
        ]
    }
    monkeypatch.setattr(rs, "load_sources", lambda: yaml_state)
    monkeypatch.setattr(rs, "save_sources", lambda d: yaml_state.update(d))

    rotated = {"name": "src", "url": "http://x", "headers": {"Authorization": "Bearer NEW"}}
    await rs.update_api_pull("src", rotated)
    assert yaml_state["api_pull"][0]["headers"]["Authorization"] == "Bearer NEW"
