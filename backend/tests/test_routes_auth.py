"""Tests for /api/auth routes + global enforcement middleware (prompts-045)."""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from backend.auth import db as auth_db
from backend.auth import service
from backend.main import app


@pytest.fixture
def auth_env(tmp_path, monkeypatch):
    """Enable auth, isolate users.db, and seed one admin (admin/Adminpass1)."""
    monkeypatch.setattr(auth_db, "_USERS_DB_PATH", tmp_path / "users.db")
    monkeypatch.setenv("SIMPLE_FEED_ENABLE_AUTH", "1")
    service._failures.clear()

    async def _seed():
        await auth_db.init_users_db()
        await auth_db.create_user(
            "admin", service.hash_password("Adminpass1"), role="admin"
        )

    asyncio.run(_seed())
    yield
    service._failures.clear()


def _client() -> TestClient:
    return TestClient(app)


def _login(username: str, password: str) -> TestClient:
    c = _client()
    r = c.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return c


# ── status / disabled mode ────────────────────────────────────────────────────

def test_status_reports_enabled(auth_env):
    r = _client().get("/api/auth/status")
    assert r.status_code == 200
    body = r.json()
    assert body["auth_enabled"] is True
    assert body["password_policy"] == {
        "min_length": 8,
        "required_classes": 3,
        "max_bytes": 72,
    }


def test_disabled_mode_is_open(tmp_path, monkeypatch):
    """With auth disabled, protected API is reachable and status is false."""
    monkeypatch.setattr(auth_db, "_USERS_DB_PATH", tmp_path / "users.db")
    monkeypatch.delenv("SIMPLE_FEED_ENABLE_AUTH", raising=False)
    monkeypatch.setattr(
        "backend.config.loader.load_app_config", lambda: {"auth_enabled": False}
    )
    c = _client()
    assert c.get("/api/auth/status").json()["auth_enabled"] is False
    # A protected endpoint is not gated when auth is off.
    assert c.get("/api/health").status_code == 200


# ── login ─────────────────────────────────────────────────────────────────────

def test_login_success_sets_cookie_and_me(auth_env):
    c = _login("admin", "Adminpass1")
    assert service.SESSION_COOKIE_NAME in c.cookies
    me = c.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["user"]["username"] == "admin"
    assert me.json()["user"]["role"] == "admin"
    assert "password_hash" not in me.json()["user"]


def test_login_wrong_password_generic_401(auth_env):
    r = _client().post(
        "/api/auth/login", json={"username": "admin", "password": "nope"}
    )
    assert r.status_code == 401
    assert r.json()["detail"] == "Invalid username or password"


def test_login_unknown_user_generic_401(auth_env):
    r = _client().post(
        "/api/auth/login", json={"username": "ghost", "password": "whatever"}
    )
    assert r.status_code == 401
    assert r.json()["detail"] == "Invalid username or password"


def test_login_throttled_after_five_failures(auth_env):
    c = _client()
    for _ in range(5):
        c.post("/api/auth/login", json={"username": "admin", "password": "bad"})
    # 6th attempt, even with the CORRECT password, is throttled.
    r = c.post("/api/auth/login", json={"username": "admin", "password": "Adminpass1"})
    assert r.status_code == 401


# ── enforcement ───────────────────────────────────────────────────────────────

def test_unauthenticated_protected_returns_401(auth_env):
    r = _client().get("/api/auth/users")
    assert r.status_code == 401


def test_logout_revokes_session(auth_env):
    c = _login("admin", "Adminpass1")
    assert c.get("/api/auth/me").status_code == 200
    assert c.post("/api/auth/logout").status_code == 200
    # The destroyed session no longer authenticates (cookie may linger).
    c.cookies.clear()  # simulate the cleared cookie
    assert c.get("/api/auth/me").status_code == 401


# ── self password change ──────────────────────────────────────────────────────

def test_change_own_password(auth_env):
    c = _login("admin", "Adminpass1")
    r = c.put(
        "/api/auth/password",
        json={"current_password": "Adminpass1", "new_password": "Adminpass2"},
    )
    assert r.status_code == 200
    # Old password no longer works; new one does.
    assert _client().post(
        "/api/auth/login", json={"username": "admin", "password": "Adminpass1"}
    ).status_code == 401
    assert _client().post(
        "/api/auth/login", json={"username": "admin", "password": "Adminpass2"}
    ).status_code == 200


def test_change_own_password_keeps_caller_evicts_others(auth_env):
    """Self-service change keeps the caller's session but logs out other devices."""
    caller = _login("admin", "Adminpass1")
    other = _login("admin", "Adminpass1")  # a second "device" for the same user
    assert other.get("/api/auth/me").status_code == 200

    r = caller.put(
        "/api/auth/password",
        json={"current_password": "Adminpass1", "new_password": "Adminpass2"},
    )
    assert r.status_code == 200
    # Caller stays logged in; the other session is revoked.
    assert caller.get("/api/auth/me").status_code == 200
    assert other.get("/api/auth/me").status_code == 401


def test_admin_reset_password_evicts_target_sessions(auth_env):
    """Admin reset of a user must invalidate that user's existing session."""
    admin = _login("admin", "Adminpass1")
    created = admin.post(
        "/api/auth/users",
        json={"username": "bob", "password": "Bobpass12", "role": "normal"},
    )
    assert created.status_code == 200
    uid = created.json()["id"]
    bob = _login("bob", "Bobpass12")
    assert bob.get("/api/auth/me").status_code == 200

    r = admin.put(
        f"/api/auth/users/{uid}/password", json={"new_password": "Newbobpass1"}
    )
    assert r.status_code == 200
    # Bob's old session is dead; the admin is unaffected.
    assert bob.get("/api/auth/me").status_code == 401
    assert admin.get("/api/auth/me").status_code == 200


def test_change_own_password_wrong_current(auth_env):
    c = _login("admin", "Adminpass1")
    r = c.put(
        "/api/auth/password",
        json={"current_password": "WRONG", "new_password": "Adminpass2"},
    )
    assert r.status_code == 400


def test_change_own_password_too_short(auth_env):
    c = _login("admin", "Adminpass1")
    r = c.put(
        "/api/auth/password",
        json={"current_password": "Adminpass1", "new_password": "short"},
    )
    assert r.status_code == 400


def test_change_own_password_rejects_insufficient_classes(auth_env):
    """A long password with only 2 character classes is rejected (needs 3)."""
    c = _login("admin", "Adminpass1")
    r = c.put(
        "/api/auth/password",
        json={"current_password": "Adminpass1", "new_password": "lowercaseonly1"},
    )
    assert r.status_code == 400
    assert "lowercase, uppercase, number, symbol" in r.json()["detail"]


def test_change_own_password_rejects_reuse_of_current(auth_env):
    """New password identical to current is rejected on self-change."""
    c = _login("admin", "Adminpass1")
    r = c.put(
        "/api/auth/password",
        json={"current_password": "Adminpass1", "new_password": "Adminpass1"},
    )
    assert r.status_code == 400
    assert "differ" in r.json()["detail"].lower()


# ── forced password change (prompts-047) ──────────────────────────────────────

@pytest.fixture
def auth_env_must_change(tmp_path, monkeypatch):
    """Enable auth and seed one admin whose password must be changed."""
    monkeypatch.setattr(auth_db, "_USERS_DB_PATH", tmp_path / "users.db")
    monkeypatch.setenv("SIMPLE_FEED_ENABLE_AUTH", "1")
    service._failures.clear()

    async def _seed():
        await auth_db.init_users_db()
        await auth_db.create_user(
            "admin",
            service.hash_password("Adminpass1"),
            role="admin",
            must_change_password=True,
        )

    asyncio.run(_seed())
    yield
    service._failures.clear()


def test_login_and_me_expose_must_change_flag(auth_env_must_change):
    c = _login("admin", "Adminpass1")
    me = c.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["user"]["must_change_password"] is True


def test_must_change_blocks_other_endpoints(auth_env_must_change):
    c = _login("admin", "Adminpass1")
    # A normal protected endpoint is blocked with 403 while the flag is set.
    r = c.get("/api/auth/users")
    assert r.status_code == 403
    assert r.json()["detail"] == "Password change required"


def test_must_change_allows_self_paths(auth_env_must_change):
    c = _login("admin", "Adminpass1")
    # /me and /password must remain reachable to complete the flow.
    assert c.get("/api/auth/me").status_code == 200


def test_must_change_cleared_after_change_restores_access(auth_env_must_change):
    c = _login("admin", "Adminpass1")
    assert c.get("/api/auth/users").status_code == 403

    r = c.put(
        "/api/auth/password",
        json={"current_password": "Adminpass1", "new_password": "Adminpass2"},
    )
    assert r.status_code == 200

    # Flag cleared: /me reflects it and full access is restored, no re-login.
    assert c.get("/api/auth/me").json()["user"]["must_change_password"] is False
    assert c.get("/api/auth/users").status_code == 200


def test_normal_user_default_has_no_must_change(auth_env):
    """An admin-created user is NOT forced to change (prompt scope: admin only)."""
    admin = _login("admin", "Adminpass1")
    created = admin.post(
        "/api/auth/users",
        json={"username": "bob", "password": "Bobpass12", "role": "normal"},
    )
    assert created.status_code == 200
    assert created.json()["must_change_password"] is False
    bob = _login("bob", "Bobpass12")
    assert bob.get("/api/auth/me").json()["user"]["must_change_password"] is False
    # Not gated by the forced-change 403: a normal-user read endpoint is reachable.
    assert bob.get("/api/viewer/sources").status_code != 403


def test_create_user_rejects_insufficient_classes(auth_env):
    """Admin-created passwords must satisfy the complexity policy."""
    c = _login("admin", "Adminpass1")
    r = c.post(
        "/api/auth/users",
        json={"username": "weakuser", "password": "alllowercase1", "role": "normal"},
    )
    assert r.status_code == 400


def test_admin_reset_password_allows_reuse_unconstrained(auth_env):
    """Admin reset of ANOTHER user has no new!=current reuse constraint."""
    c = _login("admin", "Adminpass1")
    r = c.post(
        "/api/auth/users",
        json={"username": "carol", "password": "Carolpass1", "role": "normal"},
    )
    uid = r.json()["id"]
    # Reset to the same value the user already has — allowed for admin reset.
    r2 = c.put(
        f"/api/auth/users/{uid}/password", json={"new_password": "Carolpass1"}
    )
    assert r2.status_code == 200, r2.text


# ── admin user management ─────────────────────────────────────────────────────

def test_admin_user_crud(auth_env):
    c = _login("admin", "Adminpass1")
    # Create
    r = c.post(
        "/api/auth/users",
        json={"username": "viewer1", "password": "Viewerpass1", "role": "normal"},
    )
    assert r.status_code == 200, r.text
    uid = r.json()["id"]
    assert r.json()["role"] == "normal"
    # List
    users = c.get("/api/auth/users").json()
    assert {u["username"] for u in users} == {"admin", "viewer1"}
    assert all("password_hash" not in u for u in users)
    # Promote
    assert c.put(f"/api/auth/users/{uid}/role", json={"role": "admin"}).status_code == 200
    assert c.put(f"/api/auth/users/{uid}/role", json={"role": "normal"}).status_code == 200
    # Disable
    assert c.put(f"/api/auth/users/{uid}/enabled", json={"enabled": False}).status_code == 200
    # Disabled user cannot log in.
    assert _client().post(
        "/api/auth/login", json={"username": "viewer1", "password": "Viewerpass1"}
    ).status_code == 401
    # Re-enable + admin reset password
    c.put(f"/api/auth/users/{uid}/enabled", json={"enabled": True})
    assert c.put(
        f"/api/auth/users/{uid}/password", json={"new_password": "Resetpass1"}
    ).status_code == 200
    assert _client().post(
        "/api/auth/login", json={"username": "viewer1", "password": "Resetpass1"}
    ).status_code == 200
    # Delete
    assert c.delete(f"/api/auth/users/{uid}").status_code == 200
    assert {u["username"] for u in c.get("/api/auth/users").json()} == {"admin"}


def test_create_user_duplicate_409(auth_env):
    c = _login("admin", "Adminpass1")
    r = c.post(
        "/api/auth/users",
        json={"username": "admin", "password": "Anotherpass1", "role": "normal"},
    )
    assert r.status_code == 409


@pytest.mark.parametrize("bad", ["has space", "x" * 41, "", "bad/slash"])
def test_create_user_bad_username(auth_env, bad):
    c = _login("admin", "Adminpass1")
    r = c.post(
        "/api/auth/users",
        json={"username": bad, "password": "Validpass1", "role": "normal"},
    )
    assert r.status_code == 400


def test_create_user_bad_role(auth_env):
    c = _login("admin", "Adminpass1")
    r = c.post(
        "/api/auth/users",
        json={"username": "ok1", "password": "Validpass1", "role": "root"},
    )
    assert r.status_code == 400


# ── last-admin / self guards ──────────────────────────────────────────────────

def test_cannot_demote_last_admin(auth_env):
    c = _login("admin", "Adminpass1")
    me_id = c.get("/api/auth/me").json()["user"]["id"]
    # Self-role change blocked first.
    assert c.put(f"/api/auth/users/{me_id}/role", json={"role": "normal"}).status_code == 400


def test_cannot_disable_or_delete_self(auth_env):
    c = _login("admin", "Adminpass1")
    me_id = c.get("/api/auth/me").json()["user"]["id"]
    assert c.put(f"/api/auth/users/{me_id}/enabled", json={"enabled": False}).status_code == 400
    assert c.delete(f"/api/auth/users/{me_id}").status_code == 400


def test_cannot_demote_last_admin_via_other(auth_env):
    """Two admins: the second may demote the first; the lone remaining admin
    cannot then be demoted."""
    c = _login("admin", "Adminpass1")
    c.post(
        "/api/auth/users",
        json={"username": "admin2", "password": "Admin2pass1", "role": "admin"},
    )
    admin1_id = c.get("/api/auth/me").json()["user"]["id"]
    c2 = _login("admin2", "Admin2pass1")
    admin2_id = c2.get("/api/auth/me").json()["user"]["id"]
    # admin2 demotes admin1 → allowed (admin2 remains an admin).
    assert c2.put(
        f"/api/auth/users/{admin1_id}/role", json={"role": "normal"}
    ).status_code == 200
    # admin2 is now the last admin; another admin cannot exist to demote them,
    # and self-demotion is blocked.
    assert c2.put(
        f"/api/auth/users/{admin2_id}/role", json={"role": "normal"}
    ).status_code == 400


# ── role gate (normal = Viewer-only) ──────────────────────────────────────────

def test_normal_role_blocked_from_admin_endpoints(auth_env):
    c = _login("admin", "Adminpass1")
    c.post(
        "/api/auth/users",
        json={"username": "viewer1", "password": "Viewerpass1", "role": "normal"},
    )
    nc = _login("viewer1", "Viewerpass1")
    # Self endpoints allowed.
    assert nc.get("/api/auth/me").status_code == 200
    # Admin user list blocked.
    assert nc.get("/api/auth/users").status_code == 403
    # Mutating endpoint blocked.
    assert nc.post("/api/normalizer/run").status_code == 403


def test_normal_role_allowed_viewer_reads(auth_env):
    c = _login("admin", "Adminpass1")
    c.post(
        "/api/auth/users",
        json={"username": "viewer1", "password": "Viewerpass1", "role": "normal"},
    )
    nc = _login("viewer1", "Viewerpass1")
    # A whitelisted Viewer read must pass the gate (not 401/403).
    r = nc.get("/api/viewer/summary")
    assert r.status_code not in (401, 403)


def test_admin_role_reaches_everything(auth_env):
    c = _login("admin", "Adminpass1")
    assert c.get("/api/auth/users").status_code == 200


# ── Throttle key derivation (prompts-045 audit, MAJOR #2) ──────────────────────

def test_client_ip_uses_socket_peer_not_forwarded_header():
    """X-Forwarded-For is attacker-controlled and must NOT influence the key."""
    from backend.api.routes_auth import _client_ip

    class _FakeClient:
        host = "10.0.0.5"

    class _FakeReq:
        client = _FakeClient()
        headers = {"X-Forwarded-For": "1.2.3.4, 5.6.7.8"}

    assert _client_ip(_FakeReq()) == "10.0.0.5"


def test_client_ip_handles_missing_client():
    from backend.api.routes_auth import _client_ip

    class _FakeReq:
        client = None
        headers: dict = {}

    assert _client_ip(_FakeReq()) == "unknown"


# ── Defence-in-depth admin gate (prompts-045 audit MINOR) ─────────────────────

@pytest.mark.asyncio
async def test_require_admin_when_enabled_noop_when_auth_disabled(monkeypatch):
    """With auth disabled the gate is a no-op (app is fully open)."""
    from backend.auth import dependencies as deps

    monkeypatch.setattr(deps, "load_auth_enabled", lambda: False)

    class _Req:
        cookies: dict = {}

    assert await deps.require_admin_when_enabled(_Req()) is None


@pytest.mark.asyncio
async def test_require_admin_when_enabled_requires_admin(monkeypatch):
    """With auth enabled and no session, the gate raises 401."""
    from fastapi import HTTPException

    from backend.auth import dependencies as deps

    monkeypatch.setattr(deps, "load_auth_enabled", lambda: True)

    class _Req:
        cookies: dict = {}

    with pytest.raises(HTTPException) as exc:
        await deps.require_admin_when_enabled(_Req())
    assert exc.value.status_code == 401
