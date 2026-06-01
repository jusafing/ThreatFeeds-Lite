"""Tests for local feed ingest (JSON, NDJSON, CSV, XML)."""
import json
import pytest


# ── Legacy JSON behaviour still works ────────────────────────────────────────

def test_valid_single_object():
    from backend.ingestion.local_feed import ingest_local_feed
    # Just ensure parse_file is called and doesn't blow up; we mock insert below


def test_parse_valid_json():
    from backend.ingestion.parsers import parse_file
    data = json.dumps({"indicator": "1.2.3.4"}).encode()
    fmt, rows = parse_file(data)
    assert fmt == "json"
    assert rows == [{"indicator": "1.2.3.4"}]


def test_parse_valid_array():
    from backend.ingestion.parsers import parse_file
    data = json.dumps([{"indicator": "a"}, {"indicator": "b"}]).encode()
    fmt, rows = parse_file(data)
    assert fmt == "json"
    assert len(rows) == 2


def test_parse_invalid_utf8():
    from backend.ingestion.parsers import parse_file
    with pytest.raises(ValueError, match="UTF-8"):
        parse_file(b"\xff\xfe invalid bytes")


def test_parse_invalid_content_treated_as_csv_or_error():
    """A totally unparseable blob should fail gracefully during detection."""
    from backend.ingestion.parsers import parse_file
    # All-garbled non-UTF8
    with pytest.raises(ValueError):
        parse_file(b"\xff\xfe bad bytes here")


def test_parse_file_too_large():
    from backend.ingestion.parsers import parse_file, MAX_FILE_SIZE
    with pytest.raises(ValueError, match="maximum allowed size"):
        parse_file(b"x" * (MAX_FILE_SIZE + 1))


def test_parse_valid_ndjson():
    from backend.ingestion.parsers import parse_file
    data = b'{"cidr":"1.10.16.0/20","rir":"apnic"}\n{"cidr":"2.20.0.0/16","rir":"arin"}\n'
    fmt, rows = parse_file(data)
    assert fmt == "ndjson"
    assert len(rows) == 2
    assert rows[0]["cidr"] == "1.10.16.0/20"
    assert rows[1]["rir"] == "arin"


def test_parse_ndjson_with_blank_lines():
    from backend.ingestion.parsers import parse_file
    data = b'\n{"a":"1"}\n\n{"b":"2"}\n   \n'
    fmt, rows = parse_file(data)
    assert fmt == "ndjson"
    assert len(rows) == 2


def test_parse_ndjson_partial_invalid():
    from backend.ingestion.parsers import parse_file
    data = b'{"a":"1"}\nnot-json\n{"b":"2"}'
    with pytest.raises(ValueError, match="NDJSON"):
        parse_file(data, fmt="ndjson")


# ── CSV ───────────────────────────────────────────────────────────────────────

def test_parse_csv():
    from backend.ingestion.parsers import parse_file
    data = b"indicator,type\n1.2.3.4,ip\nevil.com,domain\n"
    fmt, rows = parse_file(data)
    assert fmt == "csv"
    assert len(rows) == 2
    assert rows[0]["indicator"] == "1.2.3.4"


# ── XML ───────────────────────────────────────────────────────────────────────

def test_parse_xml():
    from backend.ingestion.parsers import parse_file
    data = b"<feed><entry><indicator>1.2.3.4</indicator><severity>high</severity></entry></feed>"
    fmt, rows = parse_file(data)
    assert fmt == "xml"
    assert len(rows) == 1
    assert rows[0]["indicator"] == "1.2.3.4"


# ── Full ingest (mocked DB) ───────────────────────────────────────────────────

@pytest.mark.anyio
async def test_local_feed_dedup():
    """Re-uploading the same bytes yields 0 inserted on the second call."""
    from unittest.mock import patch
    from backend.ingestion.local_feed import ingest_local_feed

    inserted_calls = []

    async def fake_insert(source_name, entry):
        return "inserted" if len(inserted_calls) == 0 else "duplicate"

    data = json.dumps([{"indicator": "1.2.3.4"}, {"indicator": "5.6.7.8"}]).encode()

    with patch("backend.ingestion.local_feed.insert_entry", side_effect=fake_insert), \
         patch("backend.ingestion.local_feed.normalise", side_effect=lambda r, **kw: r):
        result1 = await ingest_local_feed(data, "test_src")
        inserted_calls.append(result1)
        result2 = await ingest_local_feed(data, "test_src")

    assert result1["inserted"] == 2
    assert result2["inserted"] == 0
    assert result2["skipped"] == 2


# ── prompts-015: NVD-shaped JSON and TSV ingest ────────────────────────────────

@pytest.mark.anyio
async def test_ingest_nvd_shaped_json_produces_flat_entries():
    """NVD 2.0 envelope + nested cve object must yield N flattened rows."""
    from unittest.mock import patch
    from backend.ingestion.local_feed import ingest_local_feed

    captured: list[dict] = []

    async def fake_insert(source_name, entry):
        captured.append(entry)
        return "inserted"

    payload = json.dumps({
        "vulnerabilities": [
            {"cve": {"id": "CVE-2024-1", "metrics": {"score": 9.8}}},
            {"cve": {"id": "CVE-2024-2", "metrics": {"score": 5.0}}},
            {"cve": {"id": "CVE-2024-3", "metrics": {"score": 1.1}}},
        ]
    }).encode()

    with patch("backend.ingestion.local_feed.insert_entry", side_effect=fake_insert), \
         patch("backend.ingestion.local_feed.normalise", side_effect=lambda r, **kw: r):
        result = await ingest_local_feed(payload, "nvd_test")

    assert result["total_read"] == 3
    assert result["inserted"] == 3
    # The normaliser receives flattened entries — verify the dot-keys arrived
    assert any("cve.id" in e for e in captured)
    assert any("cve.metrics.score" in e for e in captured)


@pytest.mark.anyio
async def test_ingest_tab_separated_csv():
    """Tab-delimited CSV must split into multiple columns, not one."""
    from unittest.mock import patch
    from backend.ingestion.local_feed import ingest_local_feed

    captured: list[dict] = []

    async def fake_insert(source_name, entry):
        captured.append(entry)
        return "inserted"

    data = b"c2_ip\tprotocol\tport\n1.2.3.4\tHTTPS\t443\n5.6.7.8\tHTTP\t80\n"

    with patch("backend.ingestion.local_feed.insert_entry", side_effect=fake_insert), \
         patch("backend.ingestion.local_feed.normalise", side_effect=lambda r, **kw: r):
        result = await ingest_local_feed(data, "cobalt_test")

    assert result["total_read"] == 2
    assert result["inserted"] == 2
    assert captured[0]["c2_ip"] == "1.2.3.4"
    assert captured[0]["protocol"] == "HTTPS"
    assert captured[0]["port"] == "443"


# ── prompts-016: real-DB e2e — distinct rows must not collapse via dedup ──────

@pytest.mark.anyio
async def test_e2e_tsv_five_distinct_rows_all_inserted(tmp_path, monkeypatch):
    """Real ingestion of a TSV file with 5 distinct rows -> inserted=5."""
    from unittest.mock import patch
    from backend.ingestion.local_feed import ingest_local_feed
    from backend.db import manager as dbm

    monkeypatch.setattr(dbm, "DATA_DIR", tmp_path)

    data = (
        b"c2_ip\tprotocol\tport\n"
        b"1.1.1.1\tHTTPS\t443\n"
        b"2.2.2.2\tHTTP\t80\n"
        b"3.3.3.3\tHTTPS\t8443\n"
        b"4.4.4.4\tDNS\t53\n"
        b"5.5.5.5\tSSH\t22\n"
    )
    with patch("backend.ingestion.local_feed.normalise", side_effect=lambda r, **kw: r):
        result = await ingest_local_feed(data, "e2e_tsv")

    assert result["total_read"] == 5
    assert result["inserted"] == 5
    assert result["duplicates"] == 0


@pytest.mark.anyio
async def test_e2e_nvd_three_cves_all_inserted(tmp_path, monkeypatch):
    """Real ingestion of an NVD-shape JSON with 3 vulns -> inserted=3."""
    from unittest.mock import patch
    from backend.ingestion.local_feed import ingest_local_feed
    from backend.db import manager as dbm

    monkeypatch.setattr(dbm, "DATA_DIR", tmp_path)

    payload = json.dumps({
        "vulnerabilities": [
            {"cve": {"id": "CVE-2024-1", "metrics": {"score": 9.8}}},
            {"cve": {"id": "CVE-2024-2", "metrics": {"score": 5.0}}},
            {"cve": {"id": "CVE-2024-3", "metrics": {"score": 1.1}}},
        ]
    }).encode()

    with patch("backend.ingestion.local_feed.normalise", side_effect=lambda r, **kw: r):
        result = await ingest_local_feed(payload, "e2e_nvd")

    assert result["total_read"] == 3
    assert result["inserted"] == 3
    assert result["duplicates"] == 0


@pytest.mark.anyio
async def test_e2e_reingest_is_idempotent(tmp_path, monkeypatch):
    """Re-ingesting the exact same file produces 0 new inserts, all duplicates."""
    from unittest.mock import patch
    from backend.ingestion.local_feed import ingest_local_feed
    from backend.db import manager as dbm

    monkeypatch.setattr(dbm, "DATA_DIR", tmp_path)

    data = b"c2_ip\tprotocol\n1.1.1.1\tHTTPS\n2.2.2.2\tHTTP\n3.3.3.3\tDNS\n"

    with patch("backend.ingestion.local_feed.normalise", side_effect=lambda r, **kw: r):
        r1 = await ingest_local_feed(data, "e2e_idemp")
        r2 = await ingest_local_feed(data, "e2e_idemp")

    assert r1["inserted"] == 3
    assert r1["duplicates"] == 0
    assert r2["inserted"] == 0
    assert r2["duplicates"] == 3


# ── prompts-021B: compressed-upload integration ────────────────────────────────

@pytest.mark.anyio
async def test_ingest_gz_upload_round_trips_json():
    """A .gz upload of JSON entries is transparently decompressed and ingested."""
    import gzip
    from unittest.mock import patch
    from backend.ingestion.local_feed import ingest_local_feed

    inner = json.dumps([{"indicator": "1.1.1.1"}, {"indicator": "2.2.2.2"}]).encode()
    body = gzip.compress(inner)

    async def fake_insert(source_name, entry):
        return "inserted"

    with patch("backend.ingestion.local_feed.insert_entry", side_effect=fake_insert), \
         patch("backend.ingestion.local_feed.normalise", side_effect=lambda r, **kw: r):
        result = await ingest_local_feed(body, "gz_src", filename="feed.json.gz")

    assert result["inserted"] == 2
    assert result["format"] == "json"


@pytest.mark.anyio
async def test_ingest_zip_upload_round_trips_csv():
    """A .zip upload (single member) of CSV is decompressed and ingested."""
    import io
    import zipfile
    from unittest.mock import patch
    from backend.ingestion.local_feed import ingest_local_feed

    csv_bytes = b"indicator,severity\n1.1.1.1,high\n2.2.2.2,low\n"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("data.csv", csv_bytes)
    body = buf.getvalue()

    async def fake_insert(source_name, entry):
        return "inserted"

    with patch("backend.ingestion.local_feed.insert_entry", side_effect=fake_insert), \
         patch("backend.ingestion.local_feed.normalise", side_effect=lambda r, **kw: r):
        result = await ingest_local_feed(body, "zip_src", filename="bundle.zip")

    assert result["inserted"] == 2
    assert result["format"] == "csv"


@pytest.mark.anyio
async def test_ingest_corrupt_gz_returns_error_payload():
    """A corrupt .gz upload yields a friendly error, not an exception."""
    from backend.ingestion.local_feed import ingest_local_feed

    body = b"\x00\x00 not gzip at all"
    result = await ingest_local_feed(body, "bad_src", filename="feed.json.gz")

    assert result["inserted"] == 0
    assert result["total_read"] == 0
    assert result["errors"]
    assert "gzip" in result["errors"][0].lower() or "magic" in result["errors"][0].lower()
