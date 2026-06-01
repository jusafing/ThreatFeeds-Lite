"""Tests for backend.auth.service (prompts-045)."""
from __future__ import annotations

import pytest

from backend.auth import db as auth_db
from backend.auth import service
from backend.auth.service import (
    authenticate,
    bootstrap_admin_if_empty,
    clear_failures,
    create_session_for_user,
    destroy_session,
    format_credential_box,
    hash_password,
    hash_token,
    generate_session_token,
    is_throttled,
    record_failure,
    reset_admin_password,
    resolve_session,
    verify_password,
)


@pytest.fixture(autouse=True)
def _isolate_users_db(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_db, "_USERS_DB_PATH", tmp_path / "users.db")
    # Redirect the first-run credential file into the tmp dir so bootstrap
    # never writes a real secret file into the repo's data/ directory.
    monkeypatch.setattr(service, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(
        service, "_CREDENTIAL_FILE", tmp_path / "first-run-admin-credentials.txt"
    )
    # Clear the in-memory throttle ledger between tests.
    service._failures.clear()
    yield
    service._failures.clear()


# ── Password hashing ─────────────────────────────────────────────────────────

def test_hash_and_verify_password():
    h = hash_password("correct horse battery staple")
    assert h != "correct horse battery staple"
    assert verify_password("correct horse battery staple", h) is True
    assert verify_password("wrong", h) is False


def test_hash_password_rejects_empty():
    with pytest.raises(ValueError):
        hash_password("")


def test_hash_password_rejects_overlong():
    with pytest.raises(ValueError):
        hash_password("a" * 73)


def test_verify_password_safe_on_bad_input():
    assert verify_password("x", "") is False
    assert verify_password("", "x") is False
    assert verify_password("x", "not-a-bcrypt-hash") is False


def test_unique_salts():
    assert hash_password("same") != hash_password("same")


# ── Tokens ───────────────────────────────────────────────────────────────────

def test_token_is_random_and_hash_stable():
    t1 = generate_session_token()
    t2 = generate_session_token()
    assert t1 != t2
    assert hash_token(t1) == hash_token(t1)
    assert hash_token(t1) != t1  # raw token never equals its hash


# ── Sessions ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_session_create_resolve_destroy():
    await auth_db.init_users_db()
    uid = await auth_db.create_user("u", hash_password("pw"), role="normal")
    token = await create_session_for_user(uid)
    user = await resolve_session(token)
    assert user["id"] == uid
    assert "password_hash" not in user
    await destroy_session(token)
    assert await resolve_session(token) is None


@pytest.mark.asyncio
async def test_resolve_session_rejects_disabled_user():
    await auth_db.init_users_db()
    uid = await auth_db.create_user("u", hash_password("pw"))
    token = await create_session_for_user(uid)
    await auth_db.set_enabled(uid, False)
    assert await resolve_session(token) is None


@pytest.mark.asyncio
async def test_resolve_session_empty_token():
    assert await resolve_session("") is None


# ── Throttle ─────────────────────────────────────────────────────────────────

def test_throttle_after_max_failures():
    for _ in range(5):
        assert is_throttled("u", "1.2.3.4") is False
        record_failure("u", "1.2.3.4")
    assert is_throttled("u", "1.2.3.4") is True
    # Different IP is independent.
    assert is_throttled("u", "9.9.9.9") is False
    clear_failures("u", "1.2.3.4")
    assert is_throttled("u", "1.2.3.4") is False


# ── authenticate() ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_authenticate_success_clears_failures():
    await auth_db.init_users_db()
    await auth_db.create_user("alice", hash_password("secret"))
    record_failure("alice", "ip")
    user = await authenticate("alice", "secret", "ip")
    assert user is not None
    assert user["username"] == "alice"
    assert "password_hash" not in user
    assert is_throttled("alice", "ip") is False


@pytest.mark.asyncio
async def test_authenticate_wrong_password_records_failure():
    await auth_db.init_users_db()
    await auth_db.create_user("alice", hash_password("secret"))
    assert await authenticate("alice", "nope", "ip") is None
    assert "alice" in [k[0] for k in service._failures]


@pytest.mark.asyncio
async def test_authenticate_unknown_user():
    await auth_db.init_users_db()
    assert await authenticate("ghost", "x", "ip") is None


@pytest.mark.asyncio
async def test_authenticate_disabled_user():
    await auth_db.init_users_db()
    uid = await auth_db.create_user("u", hash_password("pw"))
    await auth_db.set_enabled(uid, False)
    assert await authenticate("u", "pw", "ip") is None


@pytest.mark.asyncio
async def test_authenticate_throttled_returns_none():
    await auth_db.init_users_db()
    await auth_db.create_user("u", hash_password("pw"))
    for _ in range(5):
        record_failure("u", "ip")
    # Even with correct password, throttle blocks.
    assert await authenticate("u", "pw", "ip") is None


# ── Bootstrap ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_bootstrap_creates_admin_once():
    await auth_db.init_users_db()
    pw = await bootstrap_admin_if_empty()
    assert pw
    admin = await auth_db.get_user_by_username("admin")
    assert admin["role"] == "admin"
    assert verify_password(pw, admin["password_hash"]) is True
    # Second call is a no-op.
    assert await bootstrap_admin_if_empty() is None
    assert await auth_db.count_users() == 1


@pytest.mark.asyncio
async def test_bootstrap_writes_credential_to_file_not_log(caplog):
    """MAJOR #3: the generated password must land in a 0600 file, never the log."""
    import logging
    import os
    import stat

    await auth_db.init_users_db()
    with caplog.at_level(logging.WARNING):
        pw = await bootstrap_admin_if_empty()
    assert pw

    # File exists, is owner-only, and contains the plaintext credential.
    cred_file = service._CREDENTIAL_FILE
    assert cred_file.exists()
    mode = stat.S_IMODE(os.stat(cred_file).st_mode)
    assert mode == 0o600
    contents = cred_file.read_text()
    assert pw in contents
    assert "username: admin" in contents

    # The secret must NOT appear anywhere in the log output.
    assert pw not in caplog.text
    # But the log should point operators at the file.
    assert str(cred_file) in caplog.text


@pytest.mark.asyncio
async def test_bootstrap_skips_when_users_exist():
    await auth_db.init_users_db()
    await auth_db.create_user("someone", hash_password("pw"))
    assert await bootstrap_admin_if_empty() is None


# ── must_change_password / reset_admin_password (prompts-047) ─────────────────

@pytest.mark.asyncio
async def test_bootstrap_flags_must_change_password():
    await auth_db.init_users_db()
    await bootstrap_admin_if_empty()
    admin = await auth_db.get_user_by_username("admin")
    assert admin["must_change_password"] is True


@pytest.mark.asyncio
async def test_reset_admin_password_creates_admin_when_missing():
    # No init / no users: reset must provision the admin itself.
    pw = await reset_admin_password("admin")
    assert pw
    admin = await auth_db.get_user_by_username("admin")
    assert admin is not None
    assert admin["role"] == "admin"
    assert admin["must_change_password"] is True
    assert verify_password(pw, admin["password_hash"]) is True


@pytest.mark.asyncio
async def test_reset_admin_password_resets_existing_and_flags():
    await auth_db.init_users_db()
    uid = await auth_db.create_user("admin", hash_password("oldpw"), role="admin")
    pw = await reset_admin_password("admin")
    admin = await auth_db.get_user_by_id(uid)
    assert verify_password(pw, admin["password_hash"]) is True
    assert verify_password("oldpw", admin["password_hash"]) is False
    assert admin["must_change_password"] is True


@pytest.mark.asyncio
async def test_reset_admin_password_evicts_sessions():
    await auth_db.init_users_db()
    uid = await auth_db.create_user("admin", hash_password("oldpw"), role="admin")
    token = await create_session_for_user(uid)
    assert await resolve_session(token) is not None
    await reset_admin_password("admin")
    assert await resolve_session(token) is None


@pytest.mark.asyncio
async def test_reset_admin_password_writes_credential_file_not_log(caplog):
    import logging
    import os
    import stat

    with caplog.at_level(logging.WARNING):
        pw = await reset_admin_password("admin")

    cred_file = service._CREDENTIAL_FILE
    assert cred_file.exists()
    assert stat.S_IMODE(os.stat(cred_file).st_mode) == 0o600
    contents = cred_file.read_text()
    assert pw in contents
    assert "username: admin" in contents
    # Secret never reaches the log; only the file path does.
    assert pw not in caplog.text
    assert str(cred_file) in caplog.text


# ── format_credential_box (prompts-059) ─────────────────────────────────────────

def test_format_credential_box_contains_username_and_password():
    box = format_credential_box("admin", "s3cr3t-token")
    assert "username: admin" in box
    assert "password: s3cr3t-token" in box


def test_format_credential_box_uses_hash_border_and_is_rectangular():
    box = format_credential_box("admin", "s3cr3t-token")
    rows = box.splitlines()
    # Top and bottom borders are solid '#'.
    assert set(rows[0]) == {"#"}
    assert set(rows[-1]) == {"#"}
    assert len(rows) >= 5
    # Every row is the same display width and framed by '#' on both ends.
    widths = {len(r) for r in rows}
    assert len(widths) == 1, f"rows not equal width: {widths}"
    for r in rows:
        assert r.startswith("#") and r.endswith("#")


def test_format_credential_box_title_varies_by_context():
    first = format_credential_box("admin", "pw")
    reset = format_credential_box("admin", "pw", context="reset")
    created = format_credential_box("admin", "pw", context="created")
    assert "first-run admin account" in first
    assert "password reset" in reset
    assert "provisioned" in created


def test_format_credential_box_widens_for_long_password():
    short = format_credential_box("admin", "x")
    long = format_credential_box("admin", "x" * 80)
    # A longer password must not overflow the frame — the box widens to fit.
    long_rows = long.splitlines()
    assert all(("x" * 80) not in r or len(r) == len(long_rows[0]) for r in long_rows)
    assert len(long_rows[0]) > len(short.splitlines()[0])
