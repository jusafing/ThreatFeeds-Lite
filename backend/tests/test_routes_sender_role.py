"""Tests for the 'sender' role authorization (prompts-054).

A 'sender' is a listener-only machine account: it may POST to
/api/ingest/listener and reach the self-service paths, but nothing else.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from backend.api import routes_ingest
from backend.auth import db as auth_db
from backend.auth import service
from backend.main import app


@pytest.fixture
def sender_env(tmp_path, monkeypatch):
    """Enable auth, isolate users.db, seed an admin and a 'sender' account."""
    monkeypatch.setattr(auth_db, "_USERS_DB_PATH", tmp_path / "users.db")
    monkeypatch.setenv("SIMPLE_FEED_ENABLE_AUTH", "1")
    service._failures.clear()

    async def _seed():
        await auth_db.init_users_db()
        await auth_db.create_user("admin", service.hash_password("Adminpass1"), role="admin")
        await auth_db.create_user("bot", service.hash_password("Botpass123"), role="sender")

    asyncio.run(_seed())
    yield
    service._failures.clear()


def _login(username: str, password: str) -> TestClient:
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return c


# ── role is accepted by the data layer / admin routes ───────────────────────────

def test_sender_is_a_valid_role():
    assert "sender" in auth_db.VALID_ROLES


def test_admin_can_create_sender(sender_env):
    c = _login("admin", "Adminpass1")
    r = c.post(
        "/api/auth/users",
        json={"username": "bot2", "password": "Bot2pass123", "role": "sender"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "sender"


def test_invalid_role_still_rejected(sender_env):
    c = _login("admin", "Adminpass1")
    r = c.post(
        "/api/auth/users",
        json={"username": "x", "password": "Xpasssss1", "role": "robot"},
    )
    assert r.status_code == 400
    assert "sender" in r.json()["detail"]


# ── sender authz at the middleware boundary ─────────────────────────────────────

def test_sender_can_post_listener(sender_env, monkeypatch):
    captured: dict = {}

    async def _fake_process_push(payload, source_name):
        captured["source_name"] = source_name
        return {"inserted": len(payload), "skipped": 0, "errors": []}

    monkeypatch.setattr(routes_ingest, "process_push", _fake_process_push)
    c = _login("bot", "Botpass123")
    r = c.post("/api/ingest/listener", json=[{"indicator": "1.2.3.4"}])
    assert r.status_code == 200, r.text
    assert r.json()["inserted"] == 1
    # prompts-058: the feed is named after the authenticated sending user.
    assert captured["source_name"] == "bot"


def test_sender_cannot_read_viewer(sender_env):
    c = _login("bot", "Botpass123")
    assert c.get("/api/viewer/entries?limit=1").status_code == 403


def test_sender_cannot_read_normalizer(sender_env):
    c = _login("bot", "Botpass123")
    assert c.get("/api/normalizer/entries?limit=1").status_code == 403


def test_sender_cannot_list_users(sender_env):
    c = _login("bot", "Botpass123")
    assert c.get("/api/auth/users").status_code == 403


def test_sender_cannot_post_named_push(sender_env):
    """Sender is scoped to the generic listener only, not named push endpoints."""
    c = _login("bot", "Botpass123")
    r = c.post("/api/ingest/push/somefeed", json={"indicator": "x"})
    assert r.status_code == 403


def test_sender_can_reach_self_paths(sender_env):
    c = _login("bot", "Botpass123")
    assert c.get("/api/auth/me").status_code == 200
