"""
FastAPI auth dependencies and cookie helpers (prompts-045).

These guard the ``/api/auth`` routes themselves. Global request enforcement
(401 for unauthenticated, 403 for under-privileged) lives in the middleware in
``backend.main`` so it applies uniformly to every router.
"""
from __future__ import annotations

from fastapi import HTTPException, Request, Response

from backend.auth.service import SESSION_COOKIE_NAME, resolve_session
from backend.config.loader import load_auth_enabled, load_cookie_secure


def _session_token(request: Request) -> str | None:
    return request.cookies.get(SESSION_COOKIE_NAME)


async def get_current_user(request: Request) -> dict:
    """Return the authenticated user dict, or raise 401.

    Resolves the session cookie independently of the global middleware so the
    auth routes are self-contained and unit-testable.
    """
    user = await resolve_session(_session_token(request) or "")
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


async def require_admin(request: Request) -> dict:
    """Return the authenticated user dict iff they are an admin, else 401/403."""
    user = await get_current_user(request)
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Administrator privileges required")
    return user


async def require_admin_when_enabled(request: Request) -> dict | None:
    """Defence-in-depth admin gate that respects the global auth toggle.

    The global middleware already enforces the role model, but mutating routes
    add this explicit dependency so a future change to the middleware allowlist
    cannot silently expose them. When auth is disabled the app is fully open, so
    this is a no-op; when enabled it requires an admin session.
    """
    if not load_auth_enabled():
        return None
    return await require_admin(request)


def set_session_cookie(request: Request, response: Response, token: str, max_age: int) -> None:
    """Attach the session cookie with hardened attributes.

    - HttpOnly: not readable by JS (mitigates XSS token theft).
    - SameSite=Lax: not sent on cross-site POSTs (CSRF mitigation for a
      same-origin SPA).
    - Secure: set only when the request arrived over HTTPS, so local HTTP
      development still works.
    - Path: scoped to the app's mount point (root_path) when behind a prefix.
    """
    secure = _is_secure(request)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=max_age,
        httponly=True,
        samesite="lax",
        secure=secure,
        path=_cookie_path(request),
    )


def clear_session_cookie(request: Request, response: Response) -> None:
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path=_cookie_path(request),
        httponly=True,
        samesite="lax",
        secure=_is_secure(request),
    )


def _is_secure(request: Request) -> bool:
    # An explicit operator override wins over request-derived detection so we
    # don't have to trust the spoofable X-Forwarded-Proto header in production.
    forced = load_cookie_secure()
    if forced is not None:
        return forced
    if request.url.scheme == "https":
        return True
    # Honour a proxy that terminates TLS and forwards the original scheme.
    return request.headers.get("x-forwarded-proto", "").lower() == "https"


def _cookie_path(request: Request) -> str:
    root = request.scope.get("root_path") or ""
    return root if root else "/"
