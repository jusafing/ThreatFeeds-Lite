"""Tests for backend.auth.db (prompts-045)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.auth import db as auth_db
from backend.auth.db import (
    count_admins,
    count_users,
    create_session,
    create_user,
    delete_session,
    delete_user,
    get_session,
    get_user_by_id,
    get_user_by_username,
    init_users_db,
    list_users,
    purge_expired_sessions,
    set_enabled,
    set_password,
    set_role,
)


@pytest.fixture(autouse=True)
def _isolate_users_db(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_db, "_USERS_DB_PATH", tmp_path / "users.db")
    yield


def _future(hours: int = 1) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


def _past(hours: int = 1) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


@pytest.mark.asyncio
async def test_init_is_idempotent():
    await init_users_db()
    await init_users_db()
    assert await count_users() == 0


@pytest.mark.asyncio
async def test_create_and_fetch_user():
    await init_users_db()
    uid = await create_user("alice", "hash1", role="admin")
    assert uid > 0
    by_name = await get_user_by_username("alice")
    by_id = await get_user_by_id(uid)
    assert by_name["id"] == uid
    assert by_id["username"] == "alice"
    assert by_name["role"] == "admin"
    assert by_name["enabled"] is True
    assert by_name["password_hash"] == "hash1"


@pytest.mark.asyncio
async def test_duplicate_username_rejected():
    await init_users_db()
    await create_user("bob", "h")
    with pytest.raises(Exception):
        await create_user("bob", "h2")


@pytest.mark.asyncio
async def test_invalid_role_rejected():
    await init_users_db()
    with pytest.raises(ValueError):
        await create_user("x", "h", role="superuser")


@pytest.mark.asyncio
async def test_list_users_omits_password_hash():
    await init_users_db()
    await create_user("a", "h1")
    await create_user("b", "h2")
    users = await list_users()
    assert len(users) == 2
    assert all("password_hash" not in u for u in users)
    assert [u["username"] for u in users] == ["a", "b"]


@pytest.mark.asyncio
async def test_count_admins_excludes_disabled_and_id():
    await init_users_db()
    a1 = await create_user("admin1", "h", role="admin")
    await create_user("admin2", "h", role="admin")
    await create_user("normal", "h", role="normal")
    assert await count_admins() == 2
    assert await count_admins(exclude_id=a1) == 1
    await set_enabled(a1, False)
    assert await count_admins() == 1


@pytest.mark.asyncio
async def test_set_password_role_enabled():
    await init_users_db()
    uid = await create_user("u", "old")
    assert await set_password(uid, "new") is True
    assert (await get_user_by_id(uid))["password_hash"] == "new"
    assert await set_role(uid, "admin") is True
    assert (await get_user_by_id(uid))["role"] == "admin"
    assert await set_enabled(uid, False) is True
    assert (await get_user_by_id(uid))["enabled"] is False


@pytest.mark.asyncio
async def test_set_role_rejects_invalid():
    await init_users_db()
    uid = await create_user("u", "h")
    with pytest.raises(ValueError):
        await set_role(uid, "root")


@pytest.mark.asyncio
async def test_disable_user_revokes_sessions():
    await init_users_db()
    uid = await create_user("u", "h")
    await create_session("tok_hash", uid, _future())
    assert await get_session("tok_hash") is not None
    await set_enabled(uid, False)
    assert await get_session("tok_hash") is None


@pytest.mark.asyncio
async def test_set_password_revokes_all_sessions_by_default():
    """Admin reset (no keep_token_hash) must evict every session."""
    await init_users_db()
    uid = await create_user("u", "old")
    await create_session("sess_a", uid, _future())
    await create_session("sess_b", uid, _future())
    await set_password(uid, "new")
    assert await get_session("sess_a") is None
    assert await get_session("sess_b") is None


@pytest.mark.asyncio
async def test_set_password_preserves_kept_session():
    """Self-service change keeps the caller's own session, evicts the rest."""
    await init_users_db()
    uid = await create_user("u", "old")
    await create_session("keep_me", uid, _future())
    await create_session("evict_me", uid, _future())
    await set_password(uid, "new", keep_token_hash="keep_me")
    assert await get_session("keep_me") is not None
    assert await get_session("evict_me") is None


@pytest.mark.asyncio
async def test_set_password_revokes_only_target_user_sessions():
    """Resetting one user must not touch another user's sessions."""
    await init_users_db()
    u1 = await create_user("u1", "h")
    u2 = await create_user("u2", "h")
    await create_session("u1_sess", u1, _future())
    await create_session("u2_sess", u2, _future())
    await set_password(u1, "new")
    assert await get_session("u1_sess") is None
    assert await get_session("u2_sess") is not None


@pytest.mark.asyncio
async def test_delete_user_and_sessions():
    await init_users_db()
    uid = await create_user("u", "h")
    await create_session("th", uid, _future())
    assert await delete_user(uid) is True
    assert await get_user_by_id(uid) is None
    assert await get_session("th") is None
    assert await delete_user(uid) is False


@pytest.mark.asyncio
async def test_session_lifecycle():
    await init_users_db()
    uid = await create_user("u", "h")
    await create_session("th", uid, _future())
    sess = await get_session("th")
    assert sess["user_id"] == uid
    await delete_session("th")
    assert await get_session("th") is None


@pytest.mark.asyncio
async def test_expired_session_rejected_and_purged():
    await init_users_db()
    uid = await create_user("u", "h")
    await create_session("expired", uid, _past())
    await create_session("live", uid, _future())
    # Lookup of an expired token returns None (and deletes it opportunistically).
    assert await get_session("expired") is None
    assert await get_session("live") is not None


@pytest.mark.asyncio
async def test_purge_expired_sessions():
    await init_users_db()
    uid = await create_user("u", "h")
    await create_session("e1", uid, _past())
    await create_session("e2", uid, _past())
    await create_session("ok", uid, _future())
    removed = await purge_expired_sessions()
    assert removed == 2
    assert await get_session("ok") is not None


# ── must_change_password flag (prompts-047) ──────────────────────────────────

@pytest.mark.asyncio
async def test_new_user_defaults_to_no_forced_change():
    await init_users_db()
    uid = await create_user("u", "h")
    assert (await get_user_by_id(uid))["must_change_password"] is False


@pytest.mark.asyncio
async def test_create_user_with_forced_change():
    await init_users_db()
    uid = await create_user("admin", "h", role="admin", must_change_password=True)
    assert (await get_user_by_id(uid))["must_change_password"] is True


@pytest.mark.asyncio
async def test_set_password_clears_forced_change_by_default():
    await init_users_db()
    uid = await create_user("admin", "h", role="admin", must_change_password=True)
    await set_password(uid, "new")
    assert (await get_user_by_id(uid))["must_change_password"] is False


@pytest.mark.asyncio
async def test_set_password_can_set_forced_change():
    await init_users_db()
    uid = await create_user("admin", "old", role="admin")
    await set_password(uid, "reset", must_change_password=True)
    user = await get_user_by_id(uid)
    assert user["password_hash"] == "reset"
    assert user["must_change_password"] is True


@pytest.mark.asyncio
async def test_migration_adds_must_change_password_to_legacy_db(tmp_path, monkeypatch):
    """A pre-prompts-047 users.db (no must_change_password column) is upgraded
    in place by init_users_db without losing existing rows."""
    import aiosqlite

    db_path = tmp_path / "legacy.db"
    monkeypatch.setattr(auth_db, "_USERS_DB_PATH", db_path)

    # Build a v1-shaped users table by hand and insert a legacy row.
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            "CREATE TABLE users ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  username TEXT NOT NULL UNIQUE,"
            "  password_hash TEXT NOT NULL,"
            "  role TEXT NOT NULL DEFAULT 'normal',"
            "  enabled INTEGER NOT NULL DEFAULT 1,"
            "  created_at TEXT NOT NULL)"
        )
        await conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        await conn.execute("INSERT INTO schema_version (version) VALUES (1)")
        await conn.execute(
            "INSERT INTO users (username, password_hash, role, enabled, created_at) "
            "VALUES ('legacy', 'oldhash', 'admin', 1, '2020-01-01T00:00:00+00:00')"
        )
        await conn.commit()

    await init_users_db()

    user = await get_user_by_username("legacy")
    assert user is not None
    assert user["password_hash"] == "oldhash"           # data preserved
    assert user["must_change_password"] is False         # new column defaults 0

    async with aiosqlite.connect(db_path) as conn:
        cur = await conn.execute("SELECT version FROM schema_version LIMIT 1")
        assert (await cur.fetchone())[0] == 2             # version bumped
        await cur.close()
