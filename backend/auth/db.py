"""
Authentication storage (prompts-045).

SQLite-backed users + sessions registry at ``data/users.db``.

Tables
------
users
  * id            — autoincrement PK
  * username      — unique, case-sensitive login name
  * password_hash — bcrypt hash (str); never the plaintext
  * role          — 'admin' | 'normal' | 'sender'
                    ('sender' is a listener-only machine account: it may POST to
                    /api/ingest/listener and nothing else — prompts-054)
  * enabled       — 1 active, 0 disabled (cannot log in)
  * created_at    — UTC ISO8601
  * must_change_password — 1 when the current password is a generated default
                    (first-run bootstrap or --reset-admin-password); the user is
                    forced to change it before any other action (prompts-047)

sessions
  * token_hash    — SHA-256 hex of the opaque session token (PK). The raw
                    token is only ever held by the client cookie; the DB
                    stores its hash so a DB read cannot mint a valid cookie.
  * user_id       — FK-ish reference to users.id
  * created_at    — UTC ISO8601
  * expires_at    — UTC ISO8601; lookups reject expired rows

Security note: this module deals only in *hashes*. Plaintext passwords and raw
session tokens never touch disk. Hashing/token generation live in
``backend.auth.service``.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_USERS_DB_PATH = _PROJECT_ROOT / "data" / "users.db"

_USERS_SCHEMA_VERSION = 2

VALID_ROLES = frozenset({"admin", "normal", "sender"})


CREATE_USERS_TABLE = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT    NOT NULL UNIQUE,
    password_hash TEXT    NOT NULL,
    role          TEXT    NOT NULL DEFAULT 'normal',
    enabled       INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT    NOT NULL,
    must_change_password INTEGER NOT NULL DEFAULT 0
);
"""

CREATE_SESSIONS_TABLE = """
CREATE TABLE IF NOT EXISTS sessions (
    token_hash  TEXT    NOT NULL PRIMARY KEY,
    user_id     INTEGER NOT NULL,
    created_at  TEXT    NOT NULL,
    expires_at  TEXT    NOT NULL
);
"""

CREATE_SESSIONS_IDX_USER = """
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions (user_id);
"""

CREATE_SCHEMA_VERSION_TABLE = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);
"""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def init_users_db() -> None:
    """Create the users/sessions schema if absent. Idempotent.

    Also runs lightweight in-place migrations for existing databases created by
    an older schema version (no destructive operations, no data loss).
    """
    _USERS_DB_PATH.parent.mkdir(exist_ok=True)
    async with aiosqlite.connect(_USERS_DB_PATH) as db:
        await db.execute(CREATE_USERS_TABLE)
        await db.execute(CREATE_SESSIONS_TABLE)
        await db.execute(CREATE_SESSIONS_IDX_USER)
        await db.execute(CREATE_SCHEMA_VERSION_TABLE)
        await _migrate_users_schema(db)
        cur = await db.execute("SELECT version FROM schema_version LIMIT 1")
        row = await cur.fetchone()
        await cur.close()
        if row is None:
            await db.execute(
                "INSERT INTO schema_version (version) VALUES (?)",
                (_USERS_SCHEMA_VERSION,),
            )
        else:
            await db.execute(
                "UPDATE schema_version SET version = ?", (_USERS_SCHEMA_VERSION,)
            )
        await db.commit()


async def _migrate_users_schema(db: aiosqlite.Connection) -> None:
    """Idempotently bring an existing users table up to the current schema.

    v1 -> v2 (prompts-047): add the ``must_change_password`` column. SQLite's
    ``ALTER TABLE ... ADD COLUMN`` is non-destructive and existing rows take the
    column DEFAULT (0), so legacy accounts are unaffected.
    """
    cur = await db.execute("PRAGMA table_info(users)")
    cols = {row[1] for row in await cur.fetchall()}
    await cur.close()
    if "must_change_password" not in cols:
        logger.info("Migrating users schema: adding must_change_password column")
        await db.execute(
            "ALTER TABLE users ADD COLUMN "
            "must_change_password INTEGER NOT NULL DEFAULT 0"
        )


# ── User CRUD ────────────────────────────────────────────────────────────────

def _user_row_to_dict(row: Any) -> dict[str, Any]:
    return {
        "id": row[0],
        "username": row[1],
        "password_hash": row[2],
        "role": row[3],
        "enabled": bool(row[4]),
        "created_at": row[5],
        "must_change_password": bool(row[6]),
    }


_USER_COLS = "id, username, password_hash, role, enabled, created_at, must_change_password"


async def create_user(
    username: str,
    password_hash: str,
    role: str = "normal",
    *,
    must_change_password: bool = False,
) -> int:
    """Insert a new user; return its id. Raises on duplicate username."""
    if role not in VALID_ROLES:
        raise ValueError(f"invalid role: {role!r}")
    async with aiosqlite.connect(_USERS_DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO users "
            "(username, password_hash, role, enabled, created_at, must_change_password) "
            "VALUES (?, ?, ?, 1, ?, ?)",
            (
                username,
                password_hash,
                role,
                _utc_now_iso(),
                1 if must_change_password else 0,
            ),
        )
        await db.commit()
        return int(cur.lastrowid)


async def get_user_by_username(username: str) -> dict[str, Any] | None:
    async with aiosqlite.connect(_USERS_DB_PATH) as db:
        cur = await db.execute(
            f"SELECT {_USER_COLS} FROM users WHERE username = ?", (username,)
        )
        row = await cur.fetchone()
        await cur.close()
    return _user_row_to_dict(row) if row else None


async def get_user_by_id(user_id: int) -> dict[str, Any] | None:
    async with aiosqlite.connect(_USERS_DB_PATH) as db:
        cur = await db.execute(
            f"SELECT {_USER_COLS} FROM users WHERE id = ?", (user_id,)
        )
        row = await cur.fetchone()
        await cur.close()
    return _user_row_to_dict(row) if row else None


async def list_users() -> list[dict[str, Any]]:
    """Return all users (without password hashes) ordered by id."""
    async with aiosqlite.connect(_USERS_DB_PATH) as db:
        cur = await db.execute(
            f"SELECT {_USER_COLS} FROM users ORDER BY id"
        )
        rows = await cur.fetchall()
        await cur.close()
    out = []
    for row in rows:
        d = _user_row_to_dict(row)
        d.pop("password_hash", None)
        out.append(d)
    return out


async def count_users() -> int:
    async with aiosqlite.connect(_USERS_DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM users")
        row = await cur.fetchone()
        await cur.close()
    return int(row[0]) if row else 0


async def count_admins(exclude_id: int | None = None) -> int:
    """Count enabled admin users, optionally excluding one id."""
    q = "SELECT COUNT(*) FROM users WHERE role = 'admin' AND enabled = 1"
    params: tuple[Any, ...] = ()
    if exclude_id is not None:
        q += " AND id != ?"
        params = (exclude_id,)
    async with aiosqlite.connect(_USERS_DB_PATH) as db:
        cur = await db.execute(q, params)
        row = await cur.fetchone()
        await cur.close()
    return int(row[0]) if row else 0


async def set_password(
    user_id: int,
    password_hash: str,
    *,
    keep_token_hash: str | None = None,
    must_change_password: bool = False,
) -> bool:
    """Update a user's password hash and revoke their sessions.

    Security (prompts-045 audit): a password change/reset must terminate any
    existing sessions, otherwise a stolen session cookie survives a reset for up
    to the session TTL. ``keep_token_hash`` preserves a single session (the
    caller's own, on self-service change) so the user is not logged out by their
    own action; an admin reset passes ``None`` to evict every session.

    ``must_change_password`` writes the force-change flag in the same UPDATE
    (prompts-047): a normal self-change or admin reset clears it (default
    False); ``--reset-admin-password`` sets it to True so the operator-supplied
    default password must be changed on next login. Returns True if a row was
    updated.
    """
    async with aiosqlite.connect(_USERS_DB_PATH) as db:
        cur = await db.execute(
            "UPDATE users SET password_hash = ?, must_change_password = ? "
            "WHERE id = ?",
            (password_hash, 1 if must_change_password else 0, user_id),
        )
        if keep_token_hash is None:
            await db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        else:
            await db.execute(
                "DELETE FROM sessions WHERE user_id = ? AND token_hash != ?",
                (user_id, keep_token_hash),
            )
        await db.commit()
        return cur.rowcount > 0


async def set_enabled(user_id: int, enabled: bool) -> bool:
    """Enable/disable a user. Disabling also revokes their sessions."""
    async with aiosqlite.connect(_USERS_DB_PATH) as db:
        cur = await db.execute(
            "UPDATE users SET enabled = ? WHERE id = ?",
            (1 if enabled else 0, user_id),
        )
        if not enabled:
            await db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        await db.commit()
        return cur.rowcount > 0


async def set_role(user_id: int, role: str) -> bool:
    if role not in VALID_ROLES:
        raise ValueError(f"invalid role: {role!r}")
    async with aiosqlite.connect(_USERS_DB_PATH) as db:
        cur = await db.execute(
            "UPDATE users SET role = ? WHERE id = ?", (role, user_id)
        )
        await db.commit()
        return cur.rowcount > 0


async def delete_user(user_id: int) -> bool:
    """Delete a user and revoke their sessions. Returns True if removed."""
    async with aiosqlite.connect(_USERS_DB_PATH) as db:
        cur = await db.execute("DELETE FROM users WHERE id = ?", (user_id,))
        await db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        await db.commit()
        return cur.rowcount > 0


# ── Session CRUD ─────────────────────────────────────────────────────────────

async def create_session(token_hash: str, user_id: int, expires_at: str) -> None:
    async with aiosqlite.connect(_USERS_DB_PATH) as db:
        await db.execute(
            "INSERT INTO sessions (token_hash, user_id, created_at, expires_at) "
            "VALUES (?, ?, ?, ?)",
            (token_hash, user_id, _utc_now_iso(), expires_at),
        )
        await db.commit()


async def get_session(token_hash: str) -> dict[str, Any] | None:
    """Return a non-expired session row by token hash, else None.

    Expired rows are deleted opportunistically on lookup.
    """
    async with aiosqlite.connect(_USERS_DB_PATH) as db:
        cur = await db.execute(
            "SELECT token_hash, user_id, created_at, expires_at "
            "FROM sessions WHERE token_hash = ?",
            (token_hash,),
        )
        row = await cur.fetchone()
        await cur.close()
        if row is None:
            return None
        expires_at = row[3]
        if _is_expired(expires_at):
            await db.execute(
                "DELETE FROM sessions WHERE token_hash = ?", (token_hash,)
            )
            await db.commit()
            return None
    return {
        "token_hash": row[0],
        "user_id": row[1],
        "created_at": row[2],
        "expires_at": row[3],
    }


async def delete_session(token_hash: str) -> None:
    async with aiosqlite.connect(_USERS_DB_PATH) as db:
        await db.execute(
            "DELETE FROM sessions WHERE token_hash = ?", (token_hash,)
        )
        await db.commit()


async def purge_expired_sessions() -> int:
    """Delete all expired sessions; return the number removed."""
    now = _utc_now_iso()
    async with aiosqlite.connect(_USERS_DB_PATH) as db:
        cur = await db.execute(
            "DELETE FROM sessions WHERE expires_at < ?", (now,)
        )
        await db.commit()
        return cur.rowcount


def _is_expired(expires_at: str) -> bool:
    try:
        exp = datetime.fromisoformat(expires_at)
    except (TypeError, ValueError):
        return True
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    return exp <= datetime.now(timezone.utc)
