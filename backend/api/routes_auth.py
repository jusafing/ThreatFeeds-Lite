"""
Authentication routes (prompts-045) — /api/auth.

Public:
  POST /api/auth/login     — exchange credentials for a session cookie
  GET  /api/auth/status    — whether auth is enabled (for the SPA bootstrap)

Authenticated (any role):
  POST /api/auth/logout    — revoke the current session
  GET  /api/auth/me        — current user profile
  PUT  /api/auth/password  — change own password

Admin only (user management):
  GET    /api/auth/users
  POST   /api/auth/users
  PUT    /api/auth/users/{user_id}/role
  PUT    /api/auth/users/{user_id}/enabled
  PUT    /api/auth/users/{user_id}/password
  DELETE /api/auth/users/{user_id}
"""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from backend.auth import db
from backend.auth.dependencies import (
    clear_session_cookie,
    get_current_user,
    require_admin,
    set_session_cookie,
)
from backend.auth.service import (
    SESSION_COOKIE_NAME,
    SESSION_TTL,
    authenticate,
    create_session_for_user,
    destroy_session,
    hash_password,
    hash_token,
    verify_password,
)
from backend.config.loader import load_auth_enabled, load_password_policy

router = APIRouter(prefix="/api/auth", tags=["auth"])

_USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,40}$")
_MAX_PASSWORD_LEN = 72  # bcrypt hard limit (fixed)

# Character-class detectors for the configurable complexity policy.
_CLASS_PATTERNS = (
    ("lowercase", re.compile(r"[a-z]")),
    ("uppercase", re.compile(r"[A-Z]")),
    ("number", re.compile(r"[0-9]")),
    ("symbol", re.compile(r"[^A-Za-z0-9]")),
)


def _password_class_count(password: str) -> int:
    """Count how many of the four character classes appear in *password*."""
    return sum(1 for _name, rx in _CLASS_PATTERNS if rx.search(password))


# ── Request models ────────────────────────────────────────────────────────────

class LoginBody(BaseModel):
    username: str
    password: str


class ChangePasswordBody(BaseModel):
    current_password: str
    new_password: str


class CreateUserBody(BaseModel):
    username: str
    password: str
    role: str = "normal"


class RoleBody(BaseModel):
    role: str


class EnabledBody(BaseModel):
    enabled: bool


class AdminPasswordBody(BaseModel):
    new_password: str


# ── Helpers ───────────────────────────────────────────────────────────────────

def _client_ip(request: Request) -> str:
    """Client IP used to key the login brute-force throttle.

    Security (prompts-045 audit, MAJOR #2): we deliberately use the real socket
    peer (`request.client.host`) and do NOT trust `X-Forwarded-For`. XFF is
    attacker-controlled, so honouring it would let a client rotate the header on
    each attempt and bypass the per-(username, ip) throttle entirely. Behind a
    trusted reverse proxy this collapses to the proxy's address, which only
    makes the throttle more conservative (per-username), never weaker. If you
    terminate behind a proxy and need true client IPs here, add an explicit
    trusted-proxy allowlist before re-introducing XFF parsing.
    """
    return request.client.host if request.client else "unknown"


def _validate_username(username: str) -> None:
    if not _USERNAME_RE.match(username or ""):
        raise HTTPException(
            status_code=400,
            detail="Username must be 1-40 chars of letters, digits, '.', '_' or '-'",
        )


def _validate_password(password: str) -> None:
    policy = load_password_policy()
    min_length = policy["min_length"]
    required_classes = policy["required_classes"]
    if not isinstance(password, str) or len(password) < min_length:
        raise HTTPException(
            status_code=400,
            detail=f"Password must be at least {min_length} characters",
        )
    if len(password.encode("utf-8")) > _MAX_PASSWORD_LEN:
        raise HTTPException(
            status_code=400,
            detail=f"Password must be at most {_MAX_PASSWORD_LEN} bytes",
        )
    if _password_class_count(password) < required_classes:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Password must include at least {required_classes} of: "
                "lowercase, uppercase, number, symbol"
            ),
        )


def _public_user(user: dict) -> dict:
    return {
        "id": user["id"],
        "username": user["username"],
        "role": user["role"],
        "enabled": user["enabled"],
        "created_at": user.get("created_at"),
        "must_change_password": bool(user.get("must_change_password")),
    }


# ── Public ────────────────────────────────────────────────────────────────────

@router.get("/status")
async def auth_status() -> dict:
    """Report whether authentication enforcement is active (public).

    Also publishes the password policy so the SPA can mirror server-side
    validation. The policy is non-sensitive (length + character-class counts).
    """
    return {
        "auth_enabled": load_auth_enabled(),
        "password_policy": load_password_policy(),
    }


@router.post("/login")
async def login(body: LoginBody, request: Request, response: Response) -> dict:
    """Authenticate and start a session. Generic error on any failure."""
    user = await authenticate(body.username, body.password, _client_ip(request))
    if user is None:
        # Single generic message — never reveal whether the username exists,
        # the password was wrong, the account is disabled, or it was throttled.
        raise HTTPException(status_code=401, detail="Invalid username or password")
    token = await create_session_for_user(user["id"])
    set_session_cookie(
        request, response, token, max_age=int(SESSION_TTL.total_seconds())
    )
    return {"user": _public_user(user)}


# ── Authenticated (any role) ──────────────────────────────────────────────────

@router.post("/logout")
async def logout(request: Request, response: Response) -> dict:
    """Revoke the current session and clear the cookie."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        await destroy_session(token)
    clear_session_cookie(request, response)
    return {"status": "logged_out"}


@router.get("/me")
async def me(user: dict = Depends(get_current_user)) -> dict:
    return {"user": _public_user(user)}


@router.put("/password")
async def change_own_password(
    body: ChangePasswordBody,
    request: Request,
    response: Response,
    user: dict = Depends(get_current_user),
) -> dict:
    """Change the caller's own password (requires the current password)."""
    full = await db.get_user_by_id(user["id"])
    if full is None or not verify_password(body.current_password, full["password_hash"]):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    if body.new_password == body.current_password:
        raise HTTPException(
            status_code=400,
            detail="New password must differ from the current password",
        )
    _validate_password(body.new_password)
    # Revoke every other session for this user (a changed password should evict
    # any other live cookie) but keep the caller's current session alive.
    token = request.cookies.get(SESSION_COOKIE_NAME)
    keep = hash_token(token) if token else None
    await db.set_password(
        user["id"], hash_password(body.new_password), keep_token_hash=keep
    )
    return {"status": "password_changed"}


# ── Admin: user management ────────────────────────────────────────────────────

@router.get("/users")
async def list_users(admin: dict = Depends(require_admin)) -> list[dict]:
    return await db.list_users()


@router.post("/users")
async def create_user(
    body: CreateUserBody, admin: dict = Depends(require_admin)
) -> dict:
    _validate_username(body.username)
    _validate_password(body.password)
    if body.role not in db.VALID_ROLES:
        raise HTTPException(status_code=400, detail="role must be 'admin', 'normal', or 'sender'")
    if await db.get_user_by_username(body.username) is not None:
        raise HTTPException(status_code=409, detail="Username already exists")
    uid = await db.create_user(
        body.username, hash_password(body.password), role=body.role
    )
    created = await db.get_user_by_id(uid)
    return _public_user(created)


@router.put("/users/{user_id}/role")
async def set_user_role(
    user_id: int, body: RoleBody, admin: dict = Depends(require_admin)
) -> dict:
    if body.role not in db.VALID_ROLES:
        raise HTTPException(status_code=400, detail="role must be 'admin', 'normal', or 'sender'")
    target = await _require_user(user_id)
    if user_id == admin["id"]:
        raise HTTPException(status_code=400, detail="Cannot change your own role")
    # Demoting the last remaining admin would lock everyone out.
    if target["role"] == "admin" and body.role != "admin":
        if await db.count_admins(exclude_id=user_id) == 0:
            raise HTTPException(status_code=400, detail="Cannot demote the last admin")
    await db.set_role(user_id, body.role)
    return _public_user(await db.get_user_by_id(user_id))


@router.put("/users/{user_id}/enabled")
async def set_user_enabled(
    user_id: int, body: EnabledBody, admin: dict = Depends(require_admin)
) -> dict:
    target = await _require_user(user_id)
    if user_id == admin["id"]:
        raise HTTPException(status_code=400, detail="Cannot disable your own account")
    if not body.enabled and target["role"] == "admin":
        if await db.count_admins(exclude_id=user_id) == 0:
            raise HTTPException(status_code=400, detail="Cannot disable the last admin")
    await db.set_enabled(user_id, body.enabled)
    return _public_user(await db.get_user_by_id(user_id))


@router.put("/users/{user_id}/password")
async def admin_reset_password(
    user_id: int, body: AdminPasswordBody, admin: dict = Depends(require_admin)
) -> dict:
    await _require_user(user_id)
    _validate_password(body.new_password)
    # Admin reset evicts ALL of the target's sessions (keep_token_hash=None),
    # so resetting a compromised account immediately logs the attacker out.
    await db.set_password(user_id, hash_password(body.new_password))
    return {"status": "password_reset"}


@router.delete("/users/{user_id}")
async def delete_user(
    user_id: int, admin: dict = Depends(require_admin)
) -> dict:
    target = await _require_user(user_id)
    if user_id == admin["id"]:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")
    if target["role"] == "admin" and await db.count_admins(exclude_id=user_id) == 0:
        raise HTTPException(status_code=400, detail="Cannot delete the last admin")
    await db.delete_user(user_id)
    return {"status": "deleted", "id": user_id}


async def _require_user(user_id: int) -> dict:
    user = await db.get_user_by_id(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user
