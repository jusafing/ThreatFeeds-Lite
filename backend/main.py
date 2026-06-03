"""
ThreatFeeds Lite — FastAPI application entry point.
Mounts all API routers; the APScheduler instance lives in backend.scheduler.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.api.routes_app import router as app_config_router
from backend.api.routes_auth import router as auth_router
from backend.api.routes_control import router as control_router
from backend.api.routes_fields import router as fields_router
from backend.api.routes_ingest import router as ingest_router
from backend.api.routes_jobs import router as jobs_router
from backend.api.routes_llm import router as llm_router
from backend.api.routes_mappings import router as mappings_router
from backend.api.routes_normalizer import router as normalizer_router
from backend.api.routes_query import router as query_router
from backend.api.routes_smart import router as smart_router
from backend.api.routes_sources import router as sources_router
from backend.api.routes_viewer import router as viewer_router
from backend.api.routes_watchers import router as watchers_router
from backend.api.routes_feed import router as feed_router
from backend.config.loader import load_app_base_prefix, load_auth_enabled
from backend.auth.db import init_users_db
from backend.auth.service import (
    SESSION_COOKIE_NAME,
    bootstrap_admin_if_empty,
    resolve_session,
)
from backend.normalizer.db import check_and_handle_schema_bump
from backend.normalizer.mappings import (
    init_mappings_db,
    migrate_yaml_manual_mappings_once,
)
from backend.normalizer.consolidated import init_consolidated_db
from backend.normalizer.proposals import init_proposals_db
from backend.normalizer.run_history import init_run_history_db
from backend.db.watchers import init_watchers_db
from backend.logging_config import setup_logging
from backend import scheduler as scheduler_mod

_LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
setup_logging(_LOG_DIR)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Reconcile normalized.db schema before scheduling jobs. On a schema
    # version bump this drops & recreates normalized.db and resets the
    # normalized flag on all source rows so the next run rebuilds.
    try:
        await check_and_handle_schema_bump()
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("Normalized DB schema reconciliation failed: %s", exc)
    try:
        await init_proposals_db()
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("Proposals DB init failed: %s", exc)
    # prompts-021F: init mapping_versions.db and run the idempotent
    # one-shot seed from yaml manual_mappings. Both calls are safe to
    # re-run on every startup.
    try:
        await init_mappings_db()
        seeded = await migrate_yaml_manual_mappings_once()
        if seeded:
            logger.info(
                "mapping_versions migration seeded %d row(s) from yaml", seeded,
            )
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("Mapping versions init/migration failed: %s", exc)
    # prompts-032: init the consolidated (global) mapping store. Separate
    # table in the same DB file; safe to re-run on every startup.
    try:
        await init_consolidated_db()
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("Consolidated mappings init failed: %s", exc)
    # prompts-039: init the run-history store (its own DB file, never wiped
    # by a normalized.db schema bump). Safe to re-run on every startup.
    try:
        await init_run_history_db()
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("Run history init failed: %s", exc)
    # issue_local_006: init the watchers store (its own DB file, never wiped by
    # a normalized.db schema bump). Safe to re-run on every startup.
    try:
        await init_watchers_db()
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("Watchers DB init failed: %s", exc)
    # prompts-045: when authentication is enabled, ensure the users/sessions
    # store exists and bootstrap a first-run admin account. When auth is
    # disabled the app stays fully open and this is skipped entirely.
    if load_auth_enabled():
        try:
            await init_users_db()
            await bootstrap_admin_if_empty()
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning("Auth init/bootstrap failed: %s", exc)
    scheduler_mod.reload()
    scheduler_mod.start()
    yield
    scheduler_mod.stop()


app = FastAPI(
    title="ThreatFeeds Lite",
    version="0.1.0",
    description="Lightweight Threat Intelligence feed receiver, normaliser, and viewer.",
    lifespan=lifespan,
    # When deployed behind a reverse proxy at a sub-path, root_path makes
    # the OpenAPI docs / schema URLs reflect the external mount point.
    # Empty string == mounted at root (default).
    root_path=load_app_base_prefix(),
)
if app.root_path:
    logger.info("Application mounted under base prefix: %s", app.root_path)

# Allow frontend dev server (Vite on :5173) during development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers
app.include_router(ingest_router)
app.include_router(viewer_router)
app.include_router(sources_router)
app.include_router(fields_router)
app.include_router(control_router)
app.include_router(normalizer_router)
app.include_router(query_router)
app.include_router(jobs_router)
app.include_router(app_config_router)
app.include_router(llm_router)
app.include_router(smart_router)
app.include_router(mappings_router)
app.include_router(auth_router)
app.include_router(watchers_router)
# Public per-watcher feed (issue_local_006). Registered before the SPA
# catch-all (defined later in this module) so /feed/watcher/<id>/ resolves to
# the renderer rather than the index.html fallback. It lives OUTSIDE /api/ so
# the auth middleware (which only guards /api/) leaves it public by design.
app.include_router(feed_router)


# ── Authentication enforcement (prompts-045) ──────────────────────────────────
#
# When auth is DISABLED (default) this middleware is a no-op and the app is
# fully open, exactly as before prompts-045. When ENABLED it gates every
# /api/* route:
#   - a small public allowlist is always reachable (login, status, health,
#     the branding logo image);
#   - everything else requires a valid session cookie (401 otherwise);
#   - 'normal' (Viewer-only) users are limited to a read allowlist (403
#     otherwise), enforced server-side independently of the hidden UI nav;
#   - 'sender' (listener-only machine) accounts may only POST to the listener
#     ingest endpoint (prompts-054).
# Non-API paths (the SPA shell + static assets) are always served so the login
# page can load; the SPA itself redirects to /login when unauthenticated.

# Exact public API paths (method-checked below).
_PUBLIC_API_PATHS = frozenset({
    "/api/health",
    "/api/auth/login",
    "/api/auth/status",
})

# Self-service paths any authenticated user may reach regardless of role.
_SELF_PATHS = frozenset({
    "/api/auth/me",
    "/api/auth/logout",
    "/api/auth/password",
})

# GET-only prefixes a 'normal' (Viewer-only) user may read. Scoped to exactly
# what the Viewer page fetches.
#
# NOTE (prompts-045 security audit): the /api/sources/*-pull list endpoints are
# DELIBERATELY excluded. They return raw source config that carries per-source
# request `headers` (API keys / Authorization tokens). Source management is an
# admin-only surface; a normal user has no need to read it and must never see
# those credentials. The endpoints are additionally redacted server-side
# (routes_sources._redact_source) as defense-in-depth.
_NORMAL_GET_PREFIXES = (
    "/api/viewer",
    "/api/normalizer/entries",
    "/api/normalizer/config",
    "/api/normalizer/summary",
    "/api/normalizer/runs",
    "/api/app/pagination-max",
    "/api/app/logo",
    "/api/smart-mappings/active",
)

# POST endpoints a 'normal' (Viewer) account may reach. The natural-language
# query endpoint (prompts-064) is a read operation expressed as a POST (it
# carries a JSON body), so it is added here rather than to the GET prefixes.
# The push-only 'sender' role is deliberately NOT granted this.
_NORMAL_POST_PATHS = (
    "/api/query/nl",
)


def _normal_role_allowed(method: str, path: str) -> bool:
    if path in _SELF_PATHS:
        return True
    if method == "GET" and any(path.startswith(p) for p in _NORMAL_GET_PREFIXES):
        return True
    if method == "POST" and path in _NORMAL_POST_PATHS:
        return True
    return False


# The only ingest path a 'sender' (listener-only machine account) may reach.
# Senders get self-service paths (to log in / change a forced password / log
# out) plus this single POST endpoint — nothing else (prompts-054).
_SENDER_POST_PATH = "/api/ingest/listener"


def _sender_role_allowed(method: str, path: str) -> bool:
    if path in _SELF_PATHS:
        return True
    return method == "POST" and path == _SENDER_POST_PATH


def _role_allowed(role: str, method: str, path: str) -> bool:
    """Authorize a non-admin role for a given request. Unknown roles fail closed."""
    if role == "normal":
        return _normal_role_allowed(method, path)
    if role == "sender":
        return _sender_role_allowed(method, path)
    return False


@app.middleware("http")
async def auth_enforcement(request, call_next):
    if not load_auth_enabled():
        return await call_next(request)

    method = request.method
    path = request.url.path

    # Only guard the API surface; serve the SPA/static unconditionally.
    if not path.startswith("/api/"):
        return await call_next(request)

    # CORS preflight carries no credentials — never block it.
    if method == "OPTIONS":
        return await call_next(request)

    # Public endpoints.
    if path in _PUBLIC_API_PATHS:
        return await call_next(request)
    if method == "GET" and path == "/api/app/logo":
        return await call_next(request)

    # Require a valid session for everything else.
    token = request.cookies.get(SESSION_COOKIE_NAME)
    user = await resolve_session(token or "")
    if user is None:
        return JSONResponse(
            status_code=401, content={"detail": "Authentication required"}
        )

    # Forced password change (prompts-047): a user whose password is a generated
    # default (first-run bootstrap or --reset-admin-password) must change it
    # before doing anything else. Allow only the self-service paths needed to
    # complete that flow — read identity (/me), change the password, and log out.
    if user.get("must_change_password") and path not in _SELF_PATHS:
        return JSONResponse(
            status_code=403, content={"detail": "Password change required"}
        )

    # Role gate: admins may reach everything; non-admin roles are constrained
    # to their allowlist ('normal' = Viewer reads; 'sender' = listener POST).
    if user.get("role") != "admin" and not _role_allowed(user.get("role", ""), method, path):
        return JSONResponse(
            status_code=403, content={"detail": "Insufficient privileges"}
        )

    request.state.user = user
    return await call_next(request)


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "version": "0.1.0"}


@app.post("/api/scheduler/reload")
async def reload_scheduler() -> dict:
    """Re-read sources.yaml + normalizer-config.yaml and reschedule all jobs."""
    scheduler_mod.reload()
    return {"status": "rescheduled"}


# ── Frontend serving ──────────────────────────────────────────────────────────
#
# In production the React SPA is built into frontend/dist/. We serve it with
# two pieces:
#   1. /assets/* (and any other static subdir) directly from disk via
#      StaticFiles — chunked JS/CSS files emitted by Vite live there.
#   2. A catch-all SPA route that returns index.html for any non-API,
#      non-static path, injecting <meta name="app-base-prefix"> so the
#      client knows the base prefix to use for routing and API calls.
#
# The injection-at-request-time approach lets the same built dist/ serve
# correctly under any prefix without a rebuild — only a backend restart.

_FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
_INDEX_FILE = _FRONTEND_DIST / "index.html"
_META_PREFIX_NAME = "app-base-prefix"


def _strip_tag(html: str, marker: str) -> str:
    """Remove a single tag from ``html`` whose opening starts with ``marker``.

    Safe for either self-closing (`<base ...>`) or attribute-only tags. Removes
    the first matching tag plus any immediately preceding newline+indent so the
    document does not accumulate blank lines on repeated renders.
    """
    if marker not in html:
        return html
    start = html.index(marker)
    end = html.index(">", start) + 1
    # Trim a preceding "\n    " (or "\n") inserted by a previous render.
    lead = start
    while lead > 0 and html[lead - 1] in " \t":
        lead -= 1
    if lead > 0 and html[lead - 1] == "\n":
        lead -= 1
    return html[:lead] + html[end:]


def _render_index_html(prefix: str) -> str:
    """Return index.html with link-generation tags injected per the contract.

    Contract (prompts-019):
      prefix == ""     → inject <base href="./">; OMIT the prefix <meta> tag
      prefix != ""     → inject <base href="<prefix>/">; inject the <meta> tag

    A <base href> makes document-relative URLs in the SPA (asset references,
    API fetches, router-emitted hrefs) resolve consistently regardless of
    which deep route the document is loaded at. Omitting the <meta> when the
    prefix is empty signals "the prefix machinery is disabled".

    Idempotent: any prior <base href=...> and prior <meta name="app-base-prefix"...>
    are stripped before injection, so repeated renders at different prefixes
    never accumulate stale tags.

    Security: ``prefix`` is validated upstream by ``_APP_PREFIX_RE`` in
    backend.config.loader to characters [A-Za-z0-9._\\-/] only, so direct
    interpolation into HTML attribute values is safe (no XSS sink). Relaxing
    that validator would require HTML-escaping here.
    """
    html = _INDEX_FILE.read_text(encoding="utf-8")

    # Strip any prior injections (order-insensitive).
    html = _strip_tag(html, '<meta name="' + _META_PREFIX_NAME + '"')
    html = _strip_tag(html, "<base href=")

    base_href = f"{prefix}/" if prefix else "./"
    base_tag = f'<base href="{base_href}">'
    meta_tag = (
        f'<meta name="{_META_PREFIX_NAME}" content="{prefix}">' if prefix else ""
    )

    # Compose the injection block: <base> first so it scopes any same-document
    # relative URLs that follow; <meta> second if applicable.
    injection = "\n    " + base_tag
    if meta_tag:
        injection += "\n    " + meta_tag

    head_idx = html.find("<head>")
    if head_idx >= 0:
        insert_at = head_idx + len("<head>")
        html = html[:insert_at] + injection + html[insert_at:]
    else:
        # No <head> — prepend.
        html = base_tag + meta_tag + html
    return html


if _FRONTEND_DIST.exists():
    # Serve hashed asset bundles (JS/CSS/fonts) directly from disk.
    _ASSETS_DIR = _FRONTEND_DIST / "assets"
    if _ASSETS_DIR.exists():
        app.mount("/assets", StaticFiles(directory=str(_ASSETS_DIR)), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_catch_all(full_path: str):
        """Serve index.html for any non-API, non-asset path (SPA fallback).

        FastAPI matches route paths internally without root_path, so
        full_path here never includes the configured prefix.
        """
        # Defensive: never swallow API/docs paths.
        if full_path.startswith(("api/", "docs", "openapi.json", "redoc")):
            raise HTTPException(status_code=404)
        # Direct non-HTML static file hit (e.g. /favicon.ico, /robots.txt)
        # under the dist root — serve verbatim with proper content type.
        if full_path:
            candidate = _FRONTEND_DIST / full_path
            if candidate.is_file() and candidate.suffix not in {".html", ""}:
                from fastapi.responses import FileResponse
                return FileResponse(str(candidate))
        # Otherwise: serve the SPA shell with the active prefix injected.
        prefix = load_app_base_prefix()
        return HTMLResponse(content=_render_index_html(prefix))

    logger.info("Serving frontend from %s", _FRONTEND_DIST)
else:

    @app.get("/")
    async def frontend_not_built() -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={
                "error": "Frontend not built.",
                "hint": "Run: ./threatfeeds-lite start",
            },
        )

    logger.warning(
        "Frontend dist not found at %s. "
        "Serving fallback error on GET /. Run './threatfeeds-lite start' to build.",
        _FRONTEND_DIST,
    )
