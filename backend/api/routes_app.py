"""
Application-config routes — read and update application.yaml.

Exposes app_base_prefix, pagination cap, and the branding logo. Lives behind
/api/app/.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from backend.auth.dependencies import require_admin_when_enabled
from backend.config.loader import (
    load_app_base_prefix,
    load_app_pagination_max,
    load_logo_path,
    save_app_base_prefix,
    save_app_pagination_max,
    save_logo_path,
)

router = APIRouter(prefix="/api/app", tags=["app"])

# ── Branding logo (prompts-045) ──────────────────────────────────────────────
#
# Uploaded images live under data/branding/. We deliberately do NOT accept SVG:
# an SVG served same-origin is a stored-XSS vector (embedded <script> executes
# when the file is opened directly). Raster formats only. Every response also
# carries X-Content-Type-Options: nosniff so a mislabelled upload cannot be
# sniffed into an executable type.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_BRANDING_DIR = _PROJECT_ROOT / "data" / "branding"
_LOGO_MAX_BYTES = 2 * 1024 * 1024  # 2 MiB
_LOGO_ALLOWED: dict[str, str] = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
_LOGO_MEDIA_BY_EXT = {ext: ct for ct, ext in _LOGO_ALLOWED.items()}


def _sniff_logo_ext(data: bytes) -> str | None:
    """Return the canonical extension for *data* by inspecting magic bytes.

    Security (prompts-045 audit, MINOR): the client-supplied Content-Type is not
    trusted on its own. We confirm the bytes are actually one of the allowed
    raster formats, which also rejects an SVG (or any text/script) renamed with
    an image MIME. Returns None when the content matches no allowed format.
    """
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return None



@router.get("/base-prefix")
async def get_base_prefix() -> dict[str, str]:
    """Return the configured app_base_prefix (empty string when unset)."""
    return {"app_base_prefix": load_app_base_prefix()}


@router.put("/base-prefix")
async def set_base_prefix(
    body: dict[str, Any],
    _admin: dict | None = Depends(require_admin_when_enabled),
) -> dict[str, Any]:
    """Set the app_base_prefix.

    Body: {"app_base_prefix": "" | "/feeds" | ...}

    Returns the saved value plus restart_required: true to signal the UI
    that the change does NOT take effect until uvicorn is restarted.
    """
    value = body.get("app_base_prefix")
    # Defensive type checks: reject bools (which are int subclasses) and any
    # non-string payload before forwarding to the loader's validator.
    if not isinstance(value, str) or isinstance(value, bool):
        raise HTTPException(
            status_code=400,
            detail="Body must contain 'app_base_prefix' as a string",
        )
    try:
        save_app_base_prefix(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"app_base_prefix": value, "restart_required": True}


@router.get("/pagination-max")
async def get_pagination_max() -> dict[str, int]:
    """Return the Normalized-viewer pagination cap (default 1000)."""
    return {"pagination_max": load_app_pagination_max()}


@router.put("/pagination-max")
async def set_pagination_max(
    body: dict[str, Any],
    _admin: dict | None = Depends(require_admin_when_enabled),
) -> dict[str, Any]:
    """Set the Normalized-viewer pagination cap.

    Body: {"pagination_max": <int in [50, 100000]>}. Takes effect immediately
    (the viewer reads it live), so no restart is required.
    """
    value = body.get("pagination_max")
    # Reject bools (int subclass) and any non-int payload before the loader.
    if isinstance(value, bool) or not isinstance(value, int):
        raise HTTPException(
            status_code=400,
            detail="Body must contain 'pagination_max' as an integer",
        )
    try:
        save_app_pagination_max(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"pagination_max": value}


# ── Branding logo endpoints (prompts-045) ────────────────────────────────────


def _resolved_logo_file() -> Path | None:
    """Return the on-disk logo path if configured and present, else None.

    Defends in depth against a tampered application.yaml: the stored path must
    resolve to a real file inside data/branding/ (no traversal escape).
    """
    rel = load_logo_path()
    if not rel:
        return None
    fp = (_PROJECT_ROOT / rel).resolve()
    branding = _BRANDING_DIR.resolve()
    try:
        fp.relative_to(branding)
    except ValueError:
        return None
    return fp if fp.is_file() else None


@router.get("/logo-info")
async def logo_info() -> dict[str, bool]:
    """Report whether a branding logo is configured (cheap boolean probe)."""
    return {"has_logo": _resolved_logo_file() is not None}


@router.get("/logo")
async def get_logo() -> FileResponse:
    """Serve the branding logo image (public). 404 when none is configured."""
    fp = _resolved_logo_file()
    if fp is None:
        raise HTTPException(status_code=404, detail="No logo configured")
    media = _LOGO_MEDIA_BY_EXT.get(fp.suffix.lower(), "application/octet-stream")
    return FileResponse(
        fp,
        media_type=media,
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-cache"},
    )


@router.post("/logo")
async def upload_logo(
    file: UploadFile = File(...),
    _admin: dict | None = Depends(require_admin_when_enabled),
) -> dict[str, Any]:
    """Upload/replace the branding logo (admin-gated by the auth middleware).

    Accepts PNG, JPEG, WebP, or GIF up to 2 MiB. SVG is rejected (stored-XSS).
    The previous logo file is removed so a format switch never leaves a stale
    image behind. The image type is determined by sniffing the file's magic
    bytes, not by trusting the client-supplied Content-Type.
    """
    if file.content_type not in _LOGO_ALLOWED:
        raise HTTPException(
            status_code=400,
            detail="Unsupported image type. Allowed: PNG, JPEG, WebP, GIF.",
        )
    # Read one byte past the cap so we can detect oversize without loading more.
    data = await file.read(_LOGO_MAX_BYTES + 1)
    if len(data) > _LOGO_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Logo exceeds the 2 MiB limit")
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    # Authoritative type check: the bytes must actually be an allowed raster
    # format, regardless of the declared Content-Type or filename.
    ext = _sniff_logo_ext(data)
    if ext is None:
        raise HTTPException(
            status_code=400,
            detail="File content is not a valid PNG, JPEG, WebP, or GIF image.",
        )

    _BRANDING_DIR.mkdir(parents=True, exist_ok=True)
    for old in _BRANDING_DIR.glob("logo.*"):
        old.unlink(missing_ok=True)
    dest = _BRANDING_DIR / f"logo{ext}"
    dest.write_bytes(data)

    rel = f"data/branding/logo{ext}"
    save_logo_path(rel)
    return {"logo_path": rel, "has_logo": True}


@router.delete("/logo")
async def delete_logo(
    _admin: dict | None = Depends(require_admin_when_enabled),
) -> dict[str, bool]:
    """Remove the branding logo and revert to the default icon (admin-gated)."""
    for old in _BRANDING_DIR.glob("logo.*"):
        old.unlink(missing_ok=True)
    save_logo_path("")
    return {"has_logo": False}
