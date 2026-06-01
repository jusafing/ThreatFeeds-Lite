"""Route tests for prompts-039: Run Now apply, run-history endpoint, active
mapping exposure, and the active-proposal archive guard.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.main import app


# ── POST /api/normalizer/run — smart apply semantics ────────────────────────

def test_run_now_smart_active_reapplies():
    """In smart mode with an active mapping, Run Now clears+reapplies the
    mapping's feeds before running, and reports reset_rows."""
    client = TestClient(app)
    with patch(
        "backend.api.routes_normalizer.load_normalizer_config",
        return_value={"enabled": True, "mode": "smart"},
    ), patch(
        "backend.api.routes_normalizer.get_active_consolidated",
        new=AsyncMock(return_value={"sources": ["feed-a", "feed-b"]}),
    ), patch(
        "backend.api.routes_normalizer.reapply_consolidated_to_sources",
        new=AsyncMock(return_value=5),
    ) as reapply, patch(
        "backend.api.routes_normalizer.run_normalizer",
        new=AsyncMock(return_value={"status": "ok", "mode": "smart", "inserted": 5}),
    ) as run:
        r = client.post("/api/normalizer/run")
    assert r.status_code == 200
    body = r.json()
    assert body["reset_rows"] == 5
    assert body["inserted"] == 5
    reapply.assert_awaited_once_with(["feed-a", "feed-b"])
    run.assert_awaited_once_with(trigger="manual")


def test_run_now_auto_mode_plain_run():
    """In auto mode Run Now does a plain run — no reapply, no reset_rows."""
    client = TestClient(app)
    with patch(
        "backend.api.routes_normalizer.load_normalizer_config",
        return_value={"enabled": True, "mode": "auto"},
    ), patch(
        "backend.api.routes_normalizer.reapply_consolidated_to_sources",
        new=AsyncMock(return_value=99),
    ) as reapply, patch(
        "backend.api.routes_normalizer.run_normalizer",
        new=AsyncMock(return_value={"status": "ok", "mode": "auto", "inserted": 0}),
    ) as run:
        r = client.post("/api/normalizer/run")
    assert r.status_code == 200
    assert "reset_rows" not in r.json()
    reapply.assert_not_awaited()
    run.assert_awaited_once_with(trigger="manual")


def test_run_now_smart_without_active_plain_run():
    """Smart mode but no active mapping → plain run (nothing to reapply)."""
    client = TestClient(app)
    with patch(
        "backend.api.routes_normalizer.load_normalizer_config",
        return_value={"enabled": True, "mode": "smart"},
    ), patch(
        "backend.api.routes_normalizer.get_active_consolidated",
        new=AsyncMock(return_value=None),
    ), patch(
        "backend.api.routes_normalizer.reapply_consolidated_to_sources",
        new=AsyncMock(return_value=1),
    ) as reapply, patch(
        "backend.api.routes_normalizer.run_normalizer",
        new=AsyncMock(return_value={"status": "ok", "mode": "smart", "inserted": 0}),
    ):
        r = client.post("/api/normalizer/run")
    assert r.status_code == 200
    assert "reset_rows" not in r.json()
    reapply.assert_not_awaited()


# ── GET /api/normalizer/runs ────────────────────────────────────────────────

def test_get_runs_returns_history():
    client = TestClient(app)
    fake = [
        {"id": 2, "trigger": "manual", "mode": "smart", "status": "ok",
         "proposal_name": "Proposal-X", "sources": ["feed-a"], "processed": 3,
         "inserted": 3, "errors": 0},
        {"id": 1, "trigger": "schedule", "mode": "auto", "status": "ok",
         "proposal_name": None, "sources": [], "processed": 0, "inserted": 0,
         "errors": 0},
    ]
    with patch(
        "backend.api.routes_normalizer.list_runs",
        new=AsyncMock(return_value=fake),
    ) as lst:
        r = client.get("/api/normalizer/runs?limit=50")
    assert r.status_code == 200
    assert r.json() == fake
    lst.assert_awaited_once_with(limit=50)


# ── GET /api/smart-mappings/active — mapping exposure ───────────────────────

def test_active_includes_full_mapping():
    client = TestClient(app)
    active = {
        "id": 7,
        "mapping": {"vendor_field": "indicator", "sev": "severity"},
        "sources": ["feed-a"],
        "field_scope": "all",
        "proposal_id": 33,
        "created_at": "2026-01-01T00:00:00+00:00",
        "note": None,
    }
    with patch(
        "backend.api.routes_smart.get_active_consolidated",
        new=AsyncMock(return_value=active),
    ), patch(
        "backend.api.routes_smart.get_proposal",
        new=AsyncMock(return_value={"proposal_name": "Proposal-33"}),
    ):
        r = client.get("/api/smart-mappings/active")
    assert r.status_code == 200
    card = r.json()["active"]
    assert card["mapping"] == {"vendor_field": "indicator", "sev": "severity"}
    assert card["field_count"] == 2
    assert card["proposal_name"] == "Proposal-33"


# ── POST /api/smart-mappings/proposals/{id}/archive — active guard ──────────

def test_archive_active_proposal_returns_409():
    client = TestClient(app)
    with patch(
        "backend.api.routes_smart.get_proposal",
        new=AsyncMock(return_value={"id": 33, "proposal_name": "Proposal-33"}),
    ), patch(
        "backend.api.routes_smart.get_active_consolidated",
        new=AsyncMock(return_value={"proposal_id": 33}),
    ), patch(
        "backend.api.routes_smart.archive_proposal",
        new=AsyncMock(return_value=None),
    ) as arch:
        r = client.post("/api/smart-mappings/proposals/33/archive")
    assert r.status_code == 409
    assert "active" in r.json()["detail"].lower()
    arch.assert_not_awaited()


def test_archive_non_active_proposal_succeeds():
    client = TestClient(app)
    with patch(
        "backend.api.routes_smart.get_proposal",
        new=AsyncMock(return_value={"id": 12, "proposal_name": "Proposal-12"}),
    ), patch(
        "backend.api.routes_smart.get_active_consolidated",
        new=AsyncMock(return_value={"proposal_id": 33}),
    ), patch(
        "backend.api.routes_smart.archive_proposal",
        new=AsyncMock(return_value=None),
    ) as arch:
        r = client.post("/api/smart-mappings/proposals/12/archive")
    assert r.status_code == 200
    assert r.json()["archived"] is True
    arch.assert_awaited_once()
