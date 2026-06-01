"""Tests for backend.api.routes_smart (021E-1)."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend.api import routes_smart as routes_smart_mod
from backend.llm import config as llm_cfg_mod
from backend.llm.errors import LLMDisabledError
from backend.main import app
from backend.normalizer import config as norm_cfg_mod
from backend.normalizer import consolidated as consolidated_mod
from backend.normalizer import mappings as mappings_mod
from backend.normalizer import proposals as proposals_mod
from backend.normalizer import smart as smart_mod


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(llm_cfg_mod, "_LLM_CONFIG_PATH", tmp_path / "llm-providers.yaml")
    monkeypatch.setattr(proposals_mod, "_PROPOSALS_DB_PATH", tmp_path / "proposals.db")
    monkeypatch.setattr(norm_cfg_mod, "_NORMALIZER_CONFIG_PATH", tmp_path / "normalizer-config.yaml")
    # prompts-021F: isolate mapping_versions.db so approve doesn't pollute
    # the real data/ directory.
    monkeypatch.setattr(mappings_mod, "_MAPPINGS_DB_PATH", tmp_path / "mapping_versions.db")
    # prompts-032 Phase D: isolate the consolidated_versions DB too.
    monkeypatch.setattr(
        consolidated_mod, "_CONSOLIDATED_DB_PATH", tmp_path / "consolidated.db",
    )
    # And isolate the per-source dirty-flag reset to a clean DATA_DIR so
    # approve_proposal_core doesn't touch real source DBs.
    import backend.db.manager as mgr
    monkeypatch.setattr(mgr, "DATA_DIR", tmp_path)
    # prompts-038: consolidated approve now clears normalized output for the
    # mapping's feeds — isolate normalized.db so it never touches real data/.
    import backend.normalizer.db as norm_db_mod
    monkeypatch.setattr(norm_db_mod, "_NORM_DB_PATH", tmp_path / "normalized.db")
    yield


def _enable_llm():
    llm_cfg_mod.save_llm_config({
        "enabled": True,
        "default_provider": "p",
        "providers": [{
            "name": "p", "kind": "openai", "base_url": "https://x",
            "model": "m", "api_key": "sk-real",
        }],
    })


# ── dry-run ────────────────────────────────────────────────────────────────


def test_dry_run_returns_400_when_source_missing():
    client = TestClient(app)
    r = client.post("/api/smart-mappings/dry-run", json={})
    assert r.status_code == 400


def test_dry_run_returns_400_when_no_samples(monkeypatch):
    async def empty(*a, **kw):
        return []

    monkeypatch.setattr(smart_mod, "query_entries", empty)
    monkeypatch.setattr(smart_mod, "query_normalized", empty)
    client = TestClient(app)
    r = client.post("/api/smart-mappings/dry-run", json={"source": "s"})
    assert r.status_code == 400


def test_dry_run_returns_prompt_without_llm_call(monkeypatch):
    async def fake_qe(source_name, limit, filters=None):
        return [{"title": "x", "url": "https://a"}]

    async def empty(*a, **kw):
        return []

    monkeypatch.setattr(smart_mod, "query_entries", fake_qe)
    monkeypatch.setattr(smart_mod, "query_normalized", empty)
    client = TestClient(app)
    r = client.post("/api/smart-mappings/dry-run", json={"source": "s", "sample_size": 5})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["source"] == "s"
    assert "title" in data["raw_fields"]
    assert "prompt_system" in data
    assert "prompt_user" in data


# ── jobs ───────────────────────────────────────────────────────────────────


def test_create_job_returns_409_when_llm_disabled():
    # No LLM config saved → enabled=false default.
    client = TestClient(app)
    r = client.post("/api/smart-mappings/jobs", json={"sources": ["s"]})
    assert r.status_code == 409


def test_create_job_returns_400_on_invalid_sample_size():
    _enable_llm()
    client = TestClient(app)
    r = client.post(
        "/api/smart-mappings/jobs",
        json={"sources": ["s"], "sample_size": 9999},
    )
    assert r.status_code == 400


def test_create_job_returns_400_when_sources_missing():
    # Structural validation runs before the LLM check, so no config needed.
    client = TestClient(app)
    for body in ({}, {"sources": []}, {"sources": ["  "]}, {"sources": "s"}):
        r = client.post("/api/smart-mappings/jobs", json=body)
        assert r.status_code == 400, body


def test_create_job_returns_400_on_bad_field_scope():
    client = TestClient(app)
    r = client.post(
        "/api/smart-mappings/jobs",
        json={"sources": ["s"], "field_scope": "bogus"},
    )
    assert r.status_code == 400


def test_create_job_consolidated_spawns_job(monkeypatch):
    """Happy path: a multi-source request returns a running job handle and
    echoes the (deduped) sources + field_scope. The runner itself is stubbed
    so no LLM call happens."""
    _enable_llm()

    async def _noop(**kwargs):
        return None

    monkeypatch.setattr(routes_smart_mod, "_run_consolidated_job", _noop)
    client = TestClient(app)
    r = client.post(
        "/api/smart-mappings/jobs",
        json={
            "sources": ["feed-a", "feed-b", "feed-a"],  # dup collapsed
            "field_scope": "configured",
            "sample_size": 10,
        },
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["state"] == "running"
    assert data["sources"] == ["feed-a", "feed-b"]
    assert data["field_scope"] == "configured"
    # prompts-034 crit-4: provider + sample_size echoed for the processing row.
    assert data["provider"] is None  # no provider given → configured default
    assert data["sample_size"] == 10
    assert "job_id" in data


def test_create_job_threads_model_override(monkeypatch):
    """prompts-034: an explicit `model` is echoed back and threaded into the
    runner shim verbatim."""
    _enable_llm()
    captured: dict[str, object] = {}

    async def _capture(**kwargs):
        captured.update(kwargs)
        return None

    monkeypatch.setattr(routes_smart_mod, "_run_consolidated_job", _capture)
    client = TestClient(app)
    r = client.post(
        "/api/smart-mappings/jobs",
        json={"sources": ["feed-a"], "model": "gpt-4o-mini"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["model"] == "gpt-4o-mini"
    assert captured["model"] == "gpt-4o-mini"


def test_create_job_blank_model_falls_back_to_none(monkeypatch):
    _enable_llm()
    captured: dict[str, object] = {}

    async def _capture(**kwargs):
        captured.update(kwargs)
        return None

    monkeypatch.setattr(routes_smart_mod, "_run_consolidated_job", _capture)
    client = TestClient(app)
    r = client.post(
        "/api/smart-mappings/jobs",
        json={"sources": ["feed-a"], "model": "   "},
    )
    assert r.status_code == 200, r.text
    assert r.json()["model"] is None
    assert captured["model"] is None


def test_create_job_rejects_non_string_model():
    _enable_llm()
    client = TestClient(app)
    r = client.post(
        "/api/smart-mappings/jobs",
        json={"sources": ["feed-a"], "model": 123},
    )
    assert r.status_code == 400


def test_get_job_404():
    client = TestClient(app)
    r = client.get("/api/smart-mappings/jobs/no-such-id")
    assert r.status_code == 404


# ── proposals: list, get, approve, reject ──────────────────────────────────


@pytest.mark.asyncio
async def test_list_proposals_returns_rows():
    await proposals_mod.insert_proposal(
        source_name="s", provider_name="p", model="m", sample_size=1,
        raw_fields=["a"], mapping={"a": "title"},
        prompt_system="", prompt_user="", llm_response_raw="",
    )
    client = TestClient(app)
    r = client.get("/api/smart-mappings/proposals?source=s")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["source_name"] == "s"


def test_get_proposal_404():
    client = TestClient(app)
    r = client.get("/api/smart-mappings/proposals/99999")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_approve_merges_and_existing_wins():
    # Pre-existing manual mapping for raw_field "a" → "url".
    norm_cfg_mod.save_normalizer_config({
        "mode": "manual",
        "enabled": True,
        "interval_minutes": 10,
        "manual_mappings": {"s": {"a": "url"}},
    })
    pid = await proposals_mod.insert_proposal(
        source_name="s", provider_name="p", model="m", sample_size=1,
        raw_fields=["a", "b"],
        mapping={"a": "title", "b": "indicator"},  # "a" should be skipped
        prompt_system="", prompt_user="", llm_response_raw="",
    )
    client = TestClient(app)
    r = client.post(f"/api/smart-mappings/proposals/{pid}/approve", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert any(s["raw_field"] == "a" for s in body["skipped_conflicts"])
    assert any(a["raw_field"] == "b" for a in body["added"])

    # Disk: existing wins, new field added.
    on_disk = norm_cfg_mod.load_normalizer_config()
    assert on_disk["manual_mappings"]["s"]["a"] == "url"  # untouched
    assert on_disk["manual_mappings"]["s"]["b"] == "indicator"  # added


@pytest.mark.asyncio
async def test_approve_in_auto_mode_returns_hint():
    norm_cfg_mod.save_normalizer_config({
        "mode": "auto",
        "enabled": True,
        "interval_minutes": 10,
        "manual_mappings": {},
    })
    pid = await proposals_mod.insert_proposal(
        source_name="s", provider_name="p", model="m", sample_size=1,
        raw_fields=["a"], mapping={"a": "title"},
        prompt_system="", prompt_user="", llm_response_raw="",
    )
    client = TestClient(app)
    r = client.post(f"/api/smart-mappings/proposals/{pid}/approve", json={})
    assert r.status_code == 200
    assert "hint" in r.json()


@pytest.mark.asyncio
async def test_approve_with_set_mode_manual_flips_mode():
    norm_cfg_mod.save_normalizer_config({
        "mode": "auto",
        "enabled": True,
        "interval_minutes": 10,
        "manual_mappings": {},
    })
    pid = await proposals_mod.insert_proposal(
        source_name="s", provider_name="p", model="m", sample_size=1,
        raw_fields=["a"], mapping={"a": "title"},
        prompt_system="", prompt_user="", llm_response_raw="",
    )
    client = TestClient(app)
    r = client.post(
        f"/api/smart-mappings/proposals/{pid}/approve",
        json={"set_mode_manual": True},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "manual"
    assert body["mode_changed"] is True
    on_disk = norm_cfg_mod.load_normalizer_config()
    assert on_disk["mode"] == "manual"


@pytest.mark.asyncio
async def test_approve_already_decided_returns_409():
    pid = await proposals_mod.insert_proposal(
        source_name="s", provider_name=None, model=None, sample_size=1,
        raw_fields=[], mapping={},
        prompt_system="", prompt_user="", llm_response_raw="",
        status="approved",
    )
    client = TestClient(app)
    r = client.post(f"/api/smart-mappings/proposals/{pid}/approve", json={})
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_reject_marks_rejected_with_note():
    pid = await proposals_mod.insert_proposal(
        source_name="s", provider_name=None, model=None, sample_size=1,
        raw_fields=[], mapping={},
        prompt_system="", prompt_user="", llm_response_raw="",
    )
    client = TestClient(app)
    r = client.post(
        f"/api/smart-mappings/proposals/{pid}/reject",
        json={"note": "nope"},
    )
    assert r.status_code == 200
    fetched = await proposals_mod.get_proposal(pid)
    assert fetched["status"] == "rejected"
    assert fetched["decided_by_note"] == "nope"


def test_reject_404():
    client = TestClient(app)
    r = client.post("/api/smart-mappings/proposals/99999/reject", json={})
    assert r.status_code == 404


# ── prompts-034: archive lifecycle ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_archive_hides_from_default_list():
    pid = await proposals_mod.insert_proposal(
        source_name="s", provider_name=None, model=None, sample_size=1,
        raw_fields=[], mapping={},
        prompt_system="", prompt_user="", llm_response_raw="",
    )
    client = TestClient(app)
    # Visible in the default (active) list before archiving.
    before = client.get("/api/smart-mappings/proposals?source=s&outcome=all")
    assert before.status_code == 200
    assert any(p["id"] == pid for p in before.json())

    r = client.post(
        f"/api/smart-mappings/proposals/{pid}/archive",
        json={"note": "duplicate"},
    )
    assert r.status_code == 200
    assert r.json() == {"proposal_id": pid, "archived": True}

    fetched = await proposals_mod.get_proposal(pid)
    assert fetched["archived"] is True

    # Hidden from the default list, visible with archived=only / all.
    after = client.get("/api/smart-mappings/proposals?source=s&outcome=all")
    assert all(p["id"] != pid for p in after.json())
    only = client.get(
        "/api/smart-mappings/proposals?source=s&outcome=all&archived=only"
    )
    assert any(p["id"] == pid for p in only.json())
    allv = client.get(
        "/api/smart-mappings/proposals?source=s&outcome=all&archived=all"
    )
    assert any(p["id"] == pid for p in allv.json())


def test_archive_404():
    client = TestClient(app)
    r = client.post("/api/smart-mappings/proposals/99999/archive", json={})
    assert r.status_code == 404


def test_list_proposals_rejects_invalid_archived_filter():
    client = TestClient(app)
    r = client.get("/api/smart-mappings/proposals?archived=bogus")
    assert r.status_code == 400


# ── prompts-021F: approve writes through mapping_versions ─────────────────────


@pytest.mark.asyncio
async def test_approve_creates_active_mapping_version(tmp_path):
    """Approving a proposal must create a new active mapping_version row
    (origin='proposal', source_proposal_id set) and regenerate the yaml
    snapshot to reflect it."""
    norm_cfg_mod.save_normalizer_config({
        "mode": "manual",
        "enabled": True,
        "interval_minutes": 10,
        "manual_mappings": {},
    })
    pid = await proposals_mod.insert_proposal(
        source_name="feed-x", provider_name="p", model="m", sample_size=5,
        raw_fields=["raw_ip"], mapping={"raw_ip": "indicator"},
        prompt_system="", prompt_user="", llm_response_raw="",
    )

    client = TestClient(app)
    r = client.post(f"/api/smart-mappings/proposals/{pid}/approve", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "mapping_version_id" in body
    vid = body["mapping_version_id"]
    assert vid > 0

    # The new version is active and linked to the proposal.
    active = await mappings_mod.get_active_version("feed-x")
    assert active is not None
    assert active["id"] == vid
    assert active["origin"] == "proposal"
    assert active["source_proposal_id"] == pid
    assert active["mapping"] == {"raw_ip": "indicator"}

    # Yaml snapshot regenerated to reflect the new active version.
    on_disk = norm_cfg_mod.load_normalizer_config()
    assert on_disk["manual_mappings"]["feed-x"] == {"raw_ip": "indicator"}


@pytest.mark.asyncio
async def test_approve_uses_active_version_not_yaml_for_existing(tmp_path):
    """When an active mapping_version exists, its mapping is the 'existing'
    side of the existing-wins merge — not the yaml block."""
    norm_cfg_mod.save_normalizer_config({
        "mode": "manual",
        "enabled": True,
        "interval_minutes": 10,
        # Yaml claims raw_a → severity, but the active version says title.
        # The merge must respect the active version.
        "manual_mappings": {"feed-y": {"raw_a": "severity"}},
    })
    v1 = await mappings_mod.create_version(
        source_name="feed-y",
        mapping={"raw_a": "title"},
        origin="manual",
    )
    await mappings_mod.activate_version(v1)

    # Proposal tries raw_a → indicator (conflict with active v1's title)
    # and adds raw_b → published_at.
    pid = await proposals_mod.insert_proposal(
        source_name="feed-y", provider_name="p", model="m", sample_size=5,
        raw_fields=["raw_a", "raw_b"],
        mapping={"raw_a": "indicator", "raw_b": "published_at"},
        prompt_system="", prompt_user="", llm_response_raw="",
    )

    client = TestClient(app)
    r = client.post(f"/api/smart-mappings/proposals/{pid}/approve", json={})
    assert r.status_code == 200, r.text
    body = r.json()

    # raw_a conflicts with active v1's mapping (title), not yaml's (severity).
    skipped = body["skipped_conflicts"]
    assert len(skipped) == 1
    assert skipped[0]["raw_field"] == "raw_a"
    assert skipped[0]["existing_canonical"] == "title"
    # raw_b is new; added.
    assert any(a["raw_field"] == "raw_b" for a in body["added"])

    # New version's mapping is the merge: raw_a → title (preserved),
    # raw_b → published_at (added).
    new_active = await mappings_mod.get_active_version("feed-y")
    assert new_active["id"] != v1  # rolled forward
    assert new_active["mapping"] == {"raw_a": "title", "raw_b": "published_at"}


@pytest.mark.asyncio
async def test_approve_marks_source_dirty_for_renormalization(tmp_path):
    """approve_proposal_core must reset normalized=0 for the source so the
    scheduler re-runs the normalizer."""
    import sqlite3
    norm_cfg_mod.save_normalizer_config({
        "mode": "manual", "enabled": True, "interval_minutes": 10,
        "manual_mappings": {},
    })

    # Seed a fake source DB with 2 normalized=1 rows.
    src_db = tmp_path / "feed-z.db"
    with sqlite3.connect(src_db) as con:
        con.execute(
            "CREATE TABLE entries (id INTEGER PRIMARY KEY, normalized INTEGER NOT NULL)"
        )
        con.execute("INSERT INTO entries (normalized) VALUES (1)")
        con.execute("INSERT INTO entries (normalized) VALUES (1)")
        con.commit()

    pid = await proposals_mod.insert_proposal(
        source_name="feed-z", provider_name="p", model="m", sample_size=5,
        raw_fields=["raw_a"], mapping={"raw_a": "title"},
        prompt_system="", prompt_user="", llm_response_raw="",
    )

    client = TestClient(app)
    r = client.post(f"/api/smart-mappings/proposals/{pid}/approve", json={})
    assert r.status_code == 200, r.text
    assert r.json()["reset_rows"] == 2

    with sqlite3.connect(src_db) as con:
        cur = con.execute("SELECT COUNT(*) FROM entries WHERE normalized=1")
        assert cur.fetchone()[0] == 0


# ── prompts-032 Phase D: re-enable, active card, consolidated approve ─────────


async def _insert_consolidated_proposal(
    *, status: str = "pending", outcome: str = "pending_review",
    mapping: dict[str, str] | None = None, sources: list[str] | None = None,
    field_scope: str = "all",
) -> int:
    return await proposals_mod.insert_proposal(
        source_name=proposals_mod.CONSOLIDATED_SENTINEL,
        provider_name="p", model="m", sample_size=6,
        raw_fields=list((mapping or {"raw_a": "title"}).keys()),
        mapping=mapping or {"raw_a": "title"},
        prompt_system="", prompt_user="", llm_response_raw="",
        status=status, outcome=outcome,
        sources=sources or ["feed-a", "feed-b"],
        field_scope=field_scope,
    )


@pytest.mark.asyncio
async def test_reenable_rejected_operator_proposal_returns_to_pending():
    pid = await proposals_mod.insert_proposal(
        source_name="s", provider_name=None, model=None, sample_size=1,
        raw_fields=[], mapping={},
        prompt_system="", prompt_user="", llm_response_raw="",
        status="rejected", outcome="rejected",
    )
    client = TestClient(app)
    r = client.post(
        f"/api/smart-mappings/proposals/{pid}/reenable", json={"note": "retry"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "pending"
    fetched = await proposals_mod.get_proposal(pid)
    assert fetched["status"] == "pending"
    assert fetched["outcome"] == "pending_review"
    assert fetched["auto_applied"] is False


@pytest.mark.asyncio
async def test_reenable_discarded_proposal_returns_409():
    pid = await proposals_mod.insert_proposal(
        source_name="s", provider_name=None, model=None, sample_size=8,
        raw_fields=[], mapping={},
        prompt_system="", prompt_user="", llm_response_raw="",
        status="rejected", outcome="discarded_below_threshold",
    )
    client = TestClient(app)
    r = client.post(f"/api/smart-mappings/proposals/{pid}/reenable", json={})
    assert r.status_code == 409
    fetched = await proposals_mod.get_proposal(pid)
    assert fetched["status"] == "rejected"  # unchanged


@pytest.mark.asyncio
async def test_reenable_non_rejected_proposal_returns_409():
    pid = await proposals_mod.insert_proposal(
        source_name="s", provider_name=None, model=None, sample_size=1,
        raw_fields=[], mapping={},
        prompt_system="", prompt_user="", llm_response_raw="",
        status="pending",
    )
    client = TestClient(app)
    r = client.post(f"/api/smart-mappings/proposals/{pid}/reenable", json={})
    assert r.status_code == 409


def test_reenable_404():
    client = TestClient(app)
    r = client.post("/api/smart-mappings/proposals/99999/reenable", json={})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_active_returns_null_when_no_consolidated_version():
    client = TestClient(app)
    r = client.get("/api/smart-mappings/active")
    assert r.status_code == 200
    assert r.json() == {"active": None}


@pytest.mark.asyncio
async def test_approve_consolidated_creates_and_activates_version():
    pid = await _insert_consolidated_proposal(
        mapping={"raw_a": "title", "raw_b": "indicator"},
        sources=["feed-a", "feed-b"], field_scope="configured",
    )
    client = TestClient(app)
    r = client.post(f"/api/smart-mappings/proposals/{pid}/approve", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["outcome"] == "approved"
    assert body["field_count"] == 2
    assert body["sources"] == ["feed-a", "feed-b"]
    vid = body["consolidated_version_id"]
    assert vid > 0

    # The proposal is linked to the new consolidated version.
    fetched = await proposals_mod.get_proposal(pid)
    assert fetched["status"] == "approved"
    assert fetched["consolidated_version_id"] == vid

    # The version is the single active consolidated mapping.
    active = await consolidated_mod.get_active_consolidated()
    assert active is not None
    assert active["id"] == vid
    assert active["mapping"] == {"raw_a": "title", "raw_b": "indicator"}
    assert active["sources"] == ["feed-a", "feed-b"]
    assert active["field_scope"] == "configured"

    # The /active endpoint reflects it.
    r2 = client.get("/api/smart-mappings/active")
    assert r2.status_code == 200
    card = r2.json()["active"]
    assert card["id"] == vid
    assert card["field_count"] == 2
    assert card["sources"] == ["feed-a", "feed-b"]
    assert card["field_scope"] == "configured"


@pytest.mark.asyncio
async def test_approve_consolidated_clears_and_resets_sources(tmp_path):
    """prompts-038: approving a consolidated proposal must clear normalized
    output for its feeds AND reset their normalized flag, so the next
    normalizer run re-applies the new mapping instead of reporting
    processed=0/inserted=0."""
    import sqlite3
    import backend.normalizer.db as norm_db_mod

    # Two feeds, each with raw rows already marked normalized=1.
    for feed in ("feed-a", "feed-b"):
        with sqlite3.connect(tmp_path / f"{feed}.db") as con:
            con.execute(
                "CREATE TABLE entries (id INTEGER PRIMARY KEY, normalized INTEGER NOT NULL)"
            )
            con.execute("INSERT INTO entries (normalized) VALUES (1)")
            con.execute("INSERT INTO entries (normalized) VALUES (1)")
            con.commit()

    # Seed normalized.db with prior output for both feeds plus an unrelated one.
    await norm_db_mod.init_norm_db()
    for feed in ("feed-a", "feed-b", "feed-other"):
        await norm_db_mod.insert_normalized(
            {"source_entry_id": 1, "source_name": feed, "title": "x"}
        )

    pid = await _insert_consolidated_proposal(
        mapping={"raw_a": "title"}, sources=["feed-a", "feed-b"],
    )
    client = TestClient(app)
    r = client.post(f"/api/smart-mappings/proposals/{pid}/approve", json={})
    assert r.status_code == 200, r.text

    # Raw flags for the mapping's feeds were reset to 0 (2 rows each).
    for feed in ("feed-a", "feed-b"):
        with sqlite3.connect(tmp_path / f"{feed}.db") as con:
            cur = con.execute("SELECT COUNT(*) FROM entries WHERE normalized=0")
            assert cur.fetchone()[0] == 2

    # Normalized output cleared for the mapping's feeds only; others untouched.
    remaining = {
        row["source_name"]
        for row in await norm_db_mod.query_normalized(limit=100)
    }
    assert remaining == {"feed-other"}


@pytest.mark.asyncio
async def test_approve_consolidated_deactivates_previous_active():
    """Approving a second consolidated proposal deactivates the first —
    only the last approved consolidated mapping is active."""
    client = TestClient(app)
    pid1 = await _insert_consolidated_proposal(mapping={"raw_a": "title"})
    r1 = client.post(f"/api/smart-mappings/proposals/{pid1}/approve", json={})
    assert r1.status_code == 200, r1.text
    vid1 = r1.json()["consolidated_version_id"]

    pid2 = await _insert_consolidated_proposal(mapping={"raw_b": "indicator"})
    r2 = client.post(f"/api/smart-mappings/proposals/{pid2}/approve", json={})
    assert r2.status_code == 200, r2.text
    vid2 = r2.json()["consolidated_version_id"]
    assert vid2 != vid1

    active = await consolidated_mod.get_active_consolidated()
    assert active["id"] == vid2
    # The first version is now inactive.
    v1 = await consolidated_mod.get_consolidated_version(vid1)
    assert v1["active"] is False


@pytest.mark.asyncio
async def test_approve_consolidated_already_decided_returns_409():
    pid = await _insert_consolidated_proposal(status="rejected", outcome="rejected")
    client = TestClient(app)
    r = client.post(f"/api/smart-mappings/proposals/{pid}/approve", json={})
    assert r.status_code == 409


# ── prompts-038: proposal_name on /active + on-demand RUN ────────────────────


@pytest.mark.asyncio
async def test_active_includes_proposal_name():
    pid = await _insert_consolidated_proposal(
        mapping={"raw_a": "title"}, sources=["feed-a"],
    )
    client = TestClient(app)
    assert client.post(
        f"/api/smart-mappings/proposals/{pid}/approve", json={},
    ).status_code == 200
    card = client.get("/api/smart-mappings/active").json()["active"]
    assert card["proposal_name"]
    assert card["proposal_name"].startswith("Proposal-")
    assert card["proposal_id"] == pid


def test_run_active_returns_409_when_no_active():
    client = TestClient(app)
    r = client.post("/api/smart-mappings/active/run")
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_run_active_reapplies_and_returns_counters(monkeypatch):
    import backend.normalizer.engine as engine_mod

    async def fake_run(trigger="manual"):
        assert trigger == "reapply"
        return {"processed": 4, "inserted": 4, "errors": 0}

    monkeypatch.setattr(engine_mod, "run_normalizer", fake_run)

    pid = await _insert_consolidated_proposal(
        mapping={"raw_a": "title"}, sources=["feed-a", "feed-b"],
    )
    client = TestClient(app)
    assert client.post(
        f"/api/smart-mappings/proposals/{pid}/approve", json={},
    ).status_code == 200

    r = client.post("/api/smart-mappings/active/run")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["processed"] == 4
    assert body["inserted"] == 4
    assert body["errors"] == 0
    assert "reset_rows" in body

