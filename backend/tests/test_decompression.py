"""
Tests for backend.ingestion.decompression (prompts-021B).

Covers the locked decisions:
  Q1 — extension AND magic-bytes must agree
  Q2 — configurable size cap (tested with a tiny override)
  Q3 — Content-Type as extension proxy when filename is absent
  Q4 — multi-member and empty zips rejected
  Q5 — decompressed payload must be plaintext JSON/NDJSON/CSV/XML
"""
from __future__ import annotations

import gzip
import io
import json
import zipfile

import pytest

from backend.ingestion import decompression as dc


def _gz(payload: bytes) -> bytes:
    return gzip.compress(payload)


def _zip_one(member_name: str, payload: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(member_name, payload)
    return buf.getvalue()


def _zip_many(members: list[tuple[str, bytes]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, payload in members:
            zf.writestr(name, payload)
    return buf.getvalue()


# ── Happy paths ──────────────────────────────────────────────────────────────
def test_gzip_round_trip_json() -> None:
    payload = json.dumps([{"indicator": "1.1.1.1"}]).encode("utf-8")
    body = _gz(payload)

    name, out = dc.decompress_if_needed("feed.json.gz", body)
    assert name == "feed.json"
    assert out == payload


def test_zip_round_trip_csv_single_member() -> None:
    payload = b"indicator,severity\n1.1.1.1,high\n2.2.2.2,low\n"
    body = _zip_one("data.csv", payload)

    name, out = dc.decompress_if_needed("bundle.zip", body)
    assert name == "data.csv"
    assert out == payload


def test_pass_through_when_no_compression_extension() -> None:
    payload = json.dumps({"a": 1}).encode("utf-8")
    name, out = dc.decompress_if_needed("plain.json", payload)
    assert name == "plain.json"
    assert out == payload


# ── Q1: extension/magic disagreement ─────────────────────────────────────────
def test_gz_extension_but_wrong_magic_raises() -> None:
    body = b"\x00\x00\x00" + json.dumps({"a": 1}).encode("utf-8")
    with pytest.raises(dc.MagicMismatchError):
        dc.decompress_if_needed("feed.json.gz", body)


def test_zip_extension_but_wrong_magic_raises() -> None:
    body = json.dumps({"a": 1}).encode("utf-8")  # No PK header
    with pytest.raises(dc.MagicMismatchError):
        dc.decompress_if_needed("bundle.zip", body)


def test_gz_magic_without_extension_passes_through() -> None:
    # Q1: magic bytes alone are not enough; without an extension or
    # content-type hint we leave the body alone.
    payload = json.dumps({"a": 1}).encode("utf-8")
    body = _gz(payload)
    name, out = dc.decompress_if_needed("mystery.dat", body)
    assert name == "mystery.dat"
    assert out == body  # untouched, not decompressed


# ── Q3: Content-Type as extension proxy ──────────────────────────────────────
def test_content_type_application_gzip_triggers_decompression() -> None:
    payload = json.dumps({"a": 1}).encode("utf-8")
    body = _gz(payload)
    name, out = dc.decompress_if_needed(
        filename=None,
        body=body,
        content_type="application/gzip",
    )
    assert out == payload
    # No filename was given, so inner name is empty.
    assert name == ""


def test_content_encoding_gzip_triggers_decompression() -> None:
    payload = json.dumps({"a": 1}).encode("utf-8")
    body = _gz(payload)
    name, out = dc.decompress_if_needed(
        filename="",
        body=body,
        content_type="application/octet-stream",
        content_encoding="gzip",
    )
    assert out == payload


# ── Q4: multi-member and empty zips ──────────────────────────────────────────
def test_multi_member_zip_rejected() -> None:
    body = _zip_many([("a.json", b"{}"), ("b.json", b"[]")])
    with pytest.raises(dc.MultiMemberZipError):
        dc.decompress_if_needed("bundle.zip", body)


def test_empty_zip_rejected() -> None:
    body = _zip_many([])
    with pytest.raises(dc.MultiMemberZipError):
        dc.decompress_if_needed("bundle.zip", body)


# ── Q5: decompressed payload must be plaintext structured ────────────────────
def test_binary_decompressed_payload_rejected() -> None:
    # Gzip of arbitrary binary noise that is not valid UTF-8.
    body = _gz(bytes(range(256)) * 4)
    with pytest.raises(dc.NotPlaintextError):
        dc.decompress_if_needed("feed.bin.gz", body)


# ── Q2: size cap ─────────────────────────────────────────────────────────────
def test_gzip_payload_exceeding_cap_rejected() -> None:
    payload = (json.dumps({"x": "y"}) + "\n").encode("utf-8") * 200
    body = _gz(payload)
    with pytest.raises(dc.DecompressedTooLargeError):
        dc.decompress_if_needed("feed.json.gz", body, max_bytes=50)


def test_zip_payload_exceeding_cap_rejected() -> None:
    payload = b"indicator,severity\n" + b"1.1.1.1,high\n" * 200
    body = _zip_one("big.csv", payload)
    with pytest.raises(dc.DecompressedTooLargeError):
        dc.decompress_if_needed("bundle.zip", body, max_bytes=50)
