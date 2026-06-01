"""
Decompression — gzip and zip pre-processing for ingest payloads (prompts-021B).

Sits in front of the parser dispatch. Both local-upload and remote-pull paths
call ``decompress_if_needed`` before handing bytes to ``parsers.parse_file``.

Design rules (locked by prompts-021B planning):
  Q1 — Extension AND magic-bytes must agree. A .gz body without the gzip
       magic prefix is rejected; a body whose magic says gzip but whose
       filename has no compression extension is passed through unchanged.
  Q2 — Decompressed size is capped (default 100 MiB; configurable via
       application.yaml's ``max_decompressed_bytes``).
  Q3 — Remote pull uses Content-Type as an extension proxy
       (application/gzip, application/x-gzip, application/zip).
  Q4 — Zip archives must contain EXACTLY one member file. Empty zips
       and multi-member zips are rejected.
  Q5 — Decompressed payload must be plaintext JSON/NDJSON/CSV/XML
       (verified by delegating to parsers.detect_format).

Only Python's stdlib is used (``gzip``, ``zipfile``); no new dependencies.
"""
from __future__ import annotations

import gzip
import io
import logging
import os
import zipfile
from typing import Final

logger = logging.getLogger(__name__)

# ── Magic bytes ──────────────────────────────────────────────────────────────
GZIP_MAGIC: Final[bytes] = b"\x1f\x8b"
ZIP_MAGIC: Final[bytes] = b"PK\x03\x04"

# Empty/sentinel zip variants (still recognised as zip files by the spec).
_ZIP_EMPTY_MAGIC: Final[bytes] = b"PK\x05\x06"
_ZIP_SPANNED_MAGIC: Final[bytes] = b"PK\x07\x08"

# ── Defaults ─────────────────────────────────────────────────────────────────
DEFAULT_MAX_DECOMPRESSED_BYTES: Final[int] = 100 * 1024 * 1024  # 100 MiB

# Content-Type fragments recognised as compression hints on remote pull.
_CONTENT_TYPE_GZIP: Final[tuple[str, ...]] = ("application/gzip", "application/x-gzip")
_CONTENT_TYPE_ZIP: Final[tuple[str, ...]] = ("application/zip", "application/x-zip-compressed")


# ── Exceptions ───────────────────────────────────────────────────────────────
class DecompressionError(ValueError):
    """Base class for all decompression-layer failures.

    Subclasses ValueError so route handlers that already translate ValueError
    to HTTP 400 surface the error naturally.
    """


class MagicMismatchError(DecompressionError):
    """Declared extension/content-type does not match the body's magic bytes."""


class MultiMemberZipError(DecompressionError):
    """Zip archive does not contain exactly one regular-file member."""


class DecompressedTooLargeError(DecompressionError):
    """Decompressed payload would exceed the configured size cap."""


class NotPlaintextError(DecompressionError):
    """Decompressed payload is not recognised JSON/NDJSON/CSV/XML."""


# ── Public API ───────────────────────────────────────────────────────────────
def decompress_if_needed(
    filename: str | None,
    body: bytes,
    *,
    content_type: str | None = None,
    content_encoding: str | None = None,
    max_bytes: int | None = None,
) -> tuple[str, bytes]:
    """Decompress ``body`` if its filename/content-type indicates compression.

    Returns ``(inner_filename, inner_bytes)``. If the payload is not
    compressed (or compression cannot be inferred), the original
    filename and body are returned unchanged.

    Detection precedence:
        1. Filename extension (``.gz`` or ``.zip``)
        2. Content-Type header (remote pull only; ignored if filename
           already indicated compression)
        3. Content-Encoding header (``gzip``) — last resort for servers
           that declare encoding instead of content-type

    Two-layer verification (per Q1 and Q5):
        - The body's magic bytes must agree with the declared extension
          OR content-type (otherwise ``MagicMismatchError``).
        - The decompressed payload must be a recognised plaintext
          structured format (otherwise ``NotPlaintextError``).

    Args:
        filename: The original filename, or None/empty if unknown
            (e.g. remote pull from a path-less URL).
        body: Raw bytes from upload or HTTP response.
        content_type: Response ``Content-Type`` header for remote pulls.
        content_encoding: Response ``Content-Encoding`` header for
            servers that put ``gzip`` here instead of in content-type.
        max_bytes: Override for the size cap. Defaults to
            ``DEFAULT_MAX_DECOMPRESSED_BYTES``.

    Raises:
        MagicMismatchError, MultiMemberZipError,
        DecompressedTooLargeError, NotPlaintextError
    """
    cap = max_bytes if max_bytes is not None else DEFAULT_MAX_DECOMPRESSED_BYTES
    ext = _extension_from_name(filename)

    # Filename extension wins; otherwise consult content-type / encoding.
    declared: str | None = ext
    if declared is None:
        declared = _extension_from_content_type(content_type)
    if declared is None and content_encoding and "gzip" in content_encoding.lower():
        declared = ".gz"

    if declared is None:
        # Pass-through: not declared as compressed. Even if magic bytes
        # happen to match gzip/zip, we do not silently re-interpret
        # (Q1 — both signals required).
        return (filename or "", body)

    inner_name, inner = _do_decompress(declared, filename, body, cap)
    _assert_plaintext(inner)
    return inner_name, inner


# ── Internals ────────────────────────────────────────────────────────────────
def _extension_from_name(filename: str | None) -> str | None:
    if not filename:
        return None
    base = os.path.basename(filename)
    _, ext = os.path.splitext(base)
    ext = ext.lower()
    if ext in (".gz", ".zip"):
        return ext
    return None


def _extension_from_content_type(content_type: str | None) -> str | None:
    if not content_type:
        return None
    ct = content_type.lower().split(";", 1)[0].strip()
    if ct in _CONTENT_TYPE_GZIP:
        return ".gz"
    if ct in _CONTENT_TYPE_ZIP:
        return ".zip"
    return None


def _do_decompress(
    declared_ext: str,
    filename: str | None,
    body: bytes,
    max_bytes: int,
) -> tuple[str, bytes]:
    if declared_ext == ".gz":
        if not body.startswith(GZIP_MAGIC):
            raise MagicMismatchError(
                "Payload declared as gzip but does not start with the gzip magic "
                "bytes (1f 8b)."
            )
        inner = _safe_gzip_decompress(body, max_bytes)
        inner_name = _strip_compression_suffix(filename, ".gz")
        return inner_name, inner

    if declared_ext == ".zip":
        if not (
            body.startswith(ZIP_MAGIC)
            or body.startswith(_ZIP_EMPTY_MAGIC)
            or body.startswith(_ZIP_SPANNED_MAGIC)
        ):
            raise MagicMismatchError(
                "Payload declared as zip but does not start with the zip magic "
                "bytes (50 4b 03 04)."
            )
        return _safe_zip_extract(body, max_bytes)

    # Should not reach here — declared_ext is constrained above.
    return (filename or "", body)


def _safe_gzip_decompress(body: bytes, max_bytes: int) -> bytes:
    """Decompress gzip body with a size cap, streaming chunk-by-chunk.

    Reads up to ``max_bytes + 1`` so we can detect overruns without
    materialising arbitrary memory.
    """
    buf = io.BytesIO()
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(body), mode="rb") as gz:
            remaining = max_bytes
            while True:
                chunk = gz.read(64 * 1024)
                if not chunk:
                    break
                remaining -= len(chunk)
                if remaining < 0:
                    raise DecompressedTooLargeError(
                        f"Decompressed gzip payload exceeds the configured "
                        f"cap of {max_bytes} bytes."
                    )
                buf.write(chunk)
    except DecompressionError:
        raise
    except (OSError, EOFError, gzip.BadGzipFile) as exc:
        raise DecompressionError(f"Gzip payload is corrupt: {exc}") from exc
    return buf.getvalue()


def _safe_zip_extract(body: bytes, max_bytes: int) -> tuple[str, bytes]:
    """Extract the sole file from a zip archive, enforcing Q4 and the size cap.

    Returns ``(member_name, member_bytes)``. Raises ``MultiMemberZipError``
    if the archive contains zero or more than one regular-file member
    (directory entries are excluded from the count).
    """
    try:
        with zipfile.ZipFile(io.BytesIO(body), mode="r") as zf:
            # Filter out directory entries.
            members = [m for m in zf.infolist() if not m.is_dir()]
            if len(members) != 1:
                raise MultiMemberZipError(
                    f"Zip archive must contain exactly one file "
                    f"(found {len(members)})."
                )
            info = members[0]
            if info.file_size > max_bytes:
                raise DecompressedTooLargeError(
                    f"Zip member '{info.filename}' would decompress to "
                    f"{info.file_size} bytes, exceeding the cap of "
                    f"{max_bytes} bytes."
                )
            # Stream the member so a lying header (file_size = 0) cannot
            # smuggle a zip-bomb past the size check.
            buf = io.BytesIO()
            with zf.open(info, mode="r") as src:
                remaining = max_bytes
                while True:
                    chunk = src.read(64 * 1024)
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    if remaining < 0:
                        raise DecompressedTooLargeError(
                            f"Zip member '{info.filename}' exceeds the cap "
                            f"of {max_bytes} bytes while decompressing."
                        )
                    buf.write(chunk)
            return info.filename, buf.getvalue()
    except DecompressionError:
        raise
    except zipfile.BadZipFile as exc:
        raise DecompressionError(f"Zip archive is corrupt: {exc}") from exc


def _strip_compression_suffix(filename: str | None, suffix: str) -> str:
    if not filename:
        return ""
    base = os.path.basename(filename)
    if base.lower().endswith(suffix):
        return base[: -len(suffix)]
    return base


def _assert_plaintext(body: bytes) -> None:
    """Verify that ``body`` is one of the supported plaintext formats.

    Delegates to ``parsers.detect_format`` so the contract here exactly
    matches what the downstream parser will accept. Anything that
    raises (UTF-8 failure, format-detect failure) is re-raised as
    ``NotPlaintextError``.
    """
    # Local import to avoid any circular-import risk during module load
    # (parsers.py is imported by many ingest modules).
    from backend.ingestion.parsers import detect_format

    try:
        detect_format(body)
    except Exception as exc:
        raise NotPlaintextError(
            "Decompressed payload is not a recognised JSON/NDJSON/CSV/XML "
            f"plaintext format: {exc}"
        ) from exc
