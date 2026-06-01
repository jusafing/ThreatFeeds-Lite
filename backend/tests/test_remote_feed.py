"""
Tests for backend.ingestion.remote_feed.

Focus (issue_local_01): a remote feed whose server advertises transport-level
``Content-Encoding: gzip`` must still ingest. httpx transparently decodes that
encoding, so ``response.content`` is already plaintext JSON — the decompression
layer must NOT try to re-decompress it (which previously raised a spurious
MagicMismatchError and discarded the whole feed, yielding inserted=0).
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from backend.ingestion.remote_feed import ingest_remote_feed

pytestmark = pytest.mark.anyio


class _FakeResponse:
    def __init__(self, content: bytes, headers: dict[str, str]):
        self.content = content
        self.headers = headers

    def raise_for_status(self) -> None:  # pragma: no cover - trivial
        return None


class _FakeAsyncClient:
    """Minimal async-context-manager stand-in for httpx.AsyncClient."""

    def __init__(self, response: _FakeResponse):
        self._response = response

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, *exc) -> bool:
        return False

    async def get(self, url: str) -> _FakeResponse:
        return self._response


def _client_factory(response: _FakeResponse):
    # httpx.AsyncClient(...) is called with kwargs; ignore them and return fake.
    return lambda *args, **kwargs: _FakeAsyncClient(response)


async def _run(payload: bytes, headers: dict[str, str]) -> dict:
    response = _FakeResponse(payload, headers)

    async def fake_insert(source_name, entry):
        return "inserted"

    with patch("backend.ingestion.remote_feed.httpx.AsyncClient", _client_factory(response)), \
         patch("backend.ingestion.remote_feed.insert_entry", side_effect=fake_insert), \
         patch("backend.ingestion.remote_feed.normalise", side_effect=lambda r, **kw: r):
        return await ingest_remote_feed("https://example.test/feed.json", "src")


async def test_content_encoding_gzip_already_decoded_ingests_cisa_kev_shape():
    # CISA KEV: {"vulnerabilities": [ {...}, {...} ]}
    payload = json.dumps(
        {"vulnerabilities": [{"cveID": "CVE-2024-0001"}, {"cveID": "CVE-2024-0002"}]}
    ).encode("utf-8")
    result = await _run(
        payload,
        {"content-type": "application/json", "content-encoding": "gzip"},
    )
    assert result["inserted"] == 2
    assert result["discarded"] == 0
    assert result["errors"] == []
    assert result["format"] == "json"


async def test_content_encoding_gzip_already_decoded_ingests_nvd_shape():
    # NVD: {"vulnerabilities": [ {"cve": {...}} ]}
    payload = json.dumps(
        {"vulnerabilities": [{"cve": {"id": "CVE-2024-1000"}}]}
    ).encode("utf-8")
    result = await _run(
        payload,
        {"content-type": "application/json", "content-encoding": "gzip"},
    )
    assert result["inserted"] == 1
    assert result["discarded"] == 0


async def test_content_encoding_gzip_already_decoded_ingests_circl_list_shape():
    # CIRCL last: a bare JSON array of CVE records.
    payload = json.dumps(
        [{"id": "CVE-2024-2000"}, {"id": "CVE-2024-2001"}, {"id": "CVE-2024-2002"}]
    ).encode("utf-8")
    result = await _run(
        payload,
        {"content-type": "application/json", "content-encoding": "gzip"},
    )
    assert result["inserted"] == 3
    assert result["discarded"] == 0
