"""Tests for the DB manager."""
import asyncio
import pytest
import tempfile
import os
from pathlib import Path
from unittest.mock import patch


@pytest.fixture
def temp_data_dir(tmp_path):
    """Redirect DATA_DIR to a temp directory for isolation."""
    with patch("backend.db.manager.DATA_DIR", tmp_path):
        yield tmp_path


@pytest.mark.asyncio
async def test_insert_and_query(temp_data_dir):
    from backend.db.manager import insert_entry, query_entries

    entry = {
        "indicator": "10.0.0.1",
        "indicator_type": "ip",
        "severity": "high",
        "source": "test_source",
        "published_at": "2024-01-01T00:00:00Z",
        "ingest_mode": "push",
    }
    inserted = await insert_entry("test_source", entry)
    assert inserted == "inserted"

    results = await query_entries(source_name="test_source")
    assert len(results) == 1
    assert results[0]["indicator"] == "10.0.0.1"


@pytest.mark.asyncio
async def test_insert_coerces_list_and_dict_core_fields(temp_data_dir):
    """
    A pushed entry with a list-valued ``tags`` (or a dict core field) must be
    stored, not silently discarded. sqlite3 cannot bind list/dict params, so
    insert_entry JSON-encodes them at the storage boundary (prompts-057).
    """
    from backend.db.manager import insert_entry, query_entries

    entry = {
        "indicator": "203.0.113.7",
        "source": "coerce_src",
        "tags": ["npm", "supply-chain", "vulnerable-package"],
        "ingest_mode": "push",
    }
    result = await insert_entry("coerce_src", entry)
    assert result == "inserted"

    rows = await query_entries(source_name="coerce_src")
    assert len(rows) == 1
    # Stored as a JSON string, and still searchable for the tag substrings.
    assert isinstance(rows[0]["tags"], str)
    assert "npm" in rows[0]["tags"]
    assert "supply-chain" in rows[0]["tags"]


@pytest.mark.asyncio
async def test_deduplication(temp_data_dir):
    from backend.db.manager import insert_entry, query_entries

    entry = {
        "indicator": "dup.example.com",
        "source": "dedup_src",
        "published_at": "2024-06-01T00:00:00Z",
        "ingest_mode": "push",
    }
    first  = await insert_entry("dedup_src", entry)
    second = await insert_entry("dedup_src", entry)

    assert first == "inserted"
    assert second == "duplicate"

    results = await query_entries(source_name="dedup_src")
    assert len(results) == 1


@pytest.mark.asyncio
async def test_reset_db(temp_data_dir):
    from backend.db.manager import insert_entry, reset_db, query_entries

    await insert_entry("reset_src", {"source": "reset_src", "indicator": "x", "published_at": "2024-01-01", "ingest_mode": "push"})
    deleted = reset_db("reset_src")
    assert len(deleted) == 1

    results = await query_entries(source_name="reset_src")
    assert results == []


@pytest.mark.asyncio
async def test_summary_counts(temp_data_dir):
    from backend.db.manager import insert_entry, get_summary

    for i in range(3):
        await insert_entry("src_a", {
            "source": "src_a", "indicator": f"ip{i}", "published_at": f"2024-01-0{i+1}", "ingest_mode": "push",
        })

    summary = await get_summary()
    total = next((s for s in summary if s["source"] == "__total__"), None)
    assert total is not None
    assert total["count"] == 3


@pytest.mark.asyncio
async def test_get_summary_ignores_normalized_db(temp_data_dir):
    """A data/normalized.db file must not be treated as a feed source."""
    from backend.db.manager import insert_entry, get_summary

    # Create a real source with one entry
    await insert_entry("real_src", {
        "source": "real_src", "indicator": "1.2.3.4",
        "published_at": "2024-01-01", "ingest_mode": "push",
    })
    # Create an empty normalized.db file (no entries table)
    (temp_data_dir / "normalized.db").touch()

    summary = await get_summary()
    sources_in_summary = {s["source"] for s in summary}
    assert "normalized" not in sources_in_summary
    assert "real_src" in sources_in_summary


@pytest.mark.asyncio
async def test_get_summary_tolerates_missing_entries_table(temp_data_dir):
    """A stray .db file with no `entries` table is skipped, not fatal."""
    import sqlite3 as _sql
    from backend.db.manager import insert_entry, get_summary

    await insert_entry("good_src", {
        "source": "good_src", "indicator": "5.6.7.8",
        "published_at": "2024-01-01", "ingest_mode": "push",
    })
    # Create a junk DB that lacks the `entries` table
    conn = _sql.connect(temp_data_dir / "junk.db")
    conn.execute("CREATE TABLE other (x INTEGER)")
    conn.commit()
    conn.close()

    # Must not raise
    summary = await get_summary()
    sources_in_summary = {s["source"] for s in summary}
    assert "good_src" in sources_in_summary
    # junk is silently skipped (logged as warning)
    assert "junk" not in sources_in_summary


# ---------------------------------------------------------------------------
# prompts-013 — dedup_key + 3-state insert_entry return
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_dedup_key_computed_on_insert(temp_data_dir):
    """Every inserted row must have a non-null dedup_key (SHA256 hex)."""
    import sqlite3 as _sql
    from backend.db.manager import insert_entry, _db_path

    await insert_entry("dk_src", {
        "source": "dk_src", "indicator": "8.8.8.8",
        "published_at": "2024-01-01", "ingest_mode": "push",
    })

    conn = _sql.connect(_db_path("dk_src"))
    row = conn.execute("SELECT dedup_key FROM entries").fetchone()
    conn.close()
    assert row is not None
    assert row[0] is not None
    assert len(row[0]) == 64  # SHA256 hex


@pytest.mark.asyncio
async def test_dedup_works_when_published_at_is_null(temp_data_dir):
    """Two inserts with NULL published_at and same indicator/title must dedup."""
    from backend.db.manager import insert_entry, query_entries

    entry = {
        "source": "null_pub_src",
        "indicator": "evil.example.com",
        "title": "same title",
        "ingest_mode": "push",
        # published_at intentionally omitted
    }
    first  = await insert_entry("null_pub_src", entry)
    second = await insert_entry("null_pub_src", entry)

    assert first == "inserted"
    assert second == "duplicate"

    results = await query_entries(source_name="null_pub_src")
    assert len(results) == 1


@pytest.mark.asyncio
async def test_insert_returns_three_state_literal(temp_data_dir):
    """insert_entry return value must be one of inserted|duplicate|error."""
    from backend.db.manager import insert_entry

    entry = {"source": "s", "indicator": "i", "ingest_mode": "push"}
    result = await insert_entry("three_state_src", entry)
    assert result in {"inserted", "duplicate", "error"}
    assert result == "inserted"


# ---------------------------------------------------------------------------
# prompts-016 — content-hash dedup_key handles non-canonical fields
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_csv_rows_without_indicator_field_are_not_collapsed(temp_data_dir):
    """Two rows that differ only in a custom (non-core) field must both insert.

    Reproduces the prompts-016 bug: a CSV with headers `c2_ip,malware` (no
    `indicator` column) used to collapse every row to the same dedup_key
    because the old hash only read `indicator/published_at/source_url/title`.
    """
    from backend.db.manager import insert_entry, query_entries

    row_a = {"c2_ip": "1.1.1.1", "malware": "emotet", "ingest_mode": "local_feed"}
    row_b = {"c2_ip": "2.2.2.2", "malware": "trickbot", "ingest_mode": "local_feed"}

    r1 = await insert_entry("csv_no_ind", row_a)
    r2 = await insert_entry("csv_no_ind", row_b)

    assert r1 == "inserted"
    assert r2 == "inserted"

    results = await query_entries(source_name="csv_no_ind")
    assert len(results) == 2
    # The custom fields survive in the `extra` blob and are merged back.
    c2_ips = {r.get("c2_ip") for r in results}
    assert c2_ips == {"1.1.1.1", "2.2.2.2"}


@pytest.mark.asyncio
async def test_nvd_shape_distinct_cves_are_inserted(temp_data_dir):
    """Three flattened NVD entries with different `cve.id` must all insert."""
    from backend.db.manager import insert_entry, query_entries

    base = {"ingest_mode": "remote_json"}
    entries = [
        {**base, "cve.id": "CVE-2024-0001", "cve.metrics.score": 9.8},
        {**base, "cve.id": "CVE-2024-0002", "cve.metrics.score": 7.5},
        {**base, "cve.id": "CVE-2024-0003", "cve.metrics.score": 5.0},
    ]
    for e in entries:
        assert await insert_entry("nvd_shape", e) == "inserted"

    results = await query_entries(source_name="nvd_shape")
    assert len(results) == 3
    assert {r.get("cve.id") for r in results} == {
        "CVE-2024-0001", "CVE-2024-0002", "CVE-2024-0003",
    }


@pytest.mark.asyncio
async def test_reingest_identical_row_is_duplicate(temp_data_dir):
    """Idempotency: re-inserting the same row content yields 'duplicate'."""
    from backend.db.manager import insert_entry, query_entries

    row = {"c2_ip": "9.9.9.9", "malware": "qakbot", "ingest_mode": "local_feed"}
    assert await insert_entry("idemp_src", row) == "inserted"
    assert await insert_entry("idemp_src", row) == "duplicate"
    assert await insert_entry("idemp_src", row) == "duplicate"

    results = await query_entries(source_name="idemp_src")
    assert len(results) == 1


@pytest.mark.asyncio
async def test_legacy_indicator_path_still_dedupes(temp_data_dir):
    """Pre-existing behaviour preserved: two rows with same indicator+title dedup."""
    from backend.db.manager import insert_entry, query_entries

    entry = {
        "source": "legacy_src",
        "indicator": "evil.example.com",
        "title": "phishing kit",
        "published_at": "2024-05-01T00:00:00Z",
        "ingest_mode": "rss_pull",
    }
    assert await insert_entry("legacy_src", entry) == "inserted"
    assert await insert_entry("legacy_src", entry) == "duplicate"

    results = await query_entries(source_name="legacy_src")
    assert len(results) == 1


def test_reset_db_preserves_users_db(temp_data_dir):
    """prompts-045: a full reset must never delete the auth store users.db."""
    from backend.db.manager import reset_db

    (temp_data_dir / "feed_a.db").write_text("x")
    (temp_data_dir / "normalized.db").write_text("x")
    (temp_data_dir / "users.db").write_text("secret")

    deleted = reset_db()

    assert "users.db" not in deleted
    assert (temp_data_dir / "users.db").exists()
    assert "feed_a.db" in deleted
    assert not (temp_data_dir / "feed_a.db").exists()


def test_reset_db_single_source_unaffected(temp_data_dir):
    """Resetting a single named source still targets only that file."""
    from backend.db.manager import reset_db

    (temp_data_dir / "feed_a.db").write_text("x")
    (temp_data_dir / "users.db").write_text("secret")

    deleted = reset_db("feed_a")

    assert deleted == ["feed_a.db"]
    assert (temp_data_dir / "users.db").exists()


@pytest.mark.asyncio
async def test_query_entries_field_filter_matches_exact_column(temp_data_dir):
    """issue_local_02: an arbitrary validated column filter returns only the
    matching rows."""
    from backend.db.manager import insert_entry, query_entries

    await insert_entry("ff_src", {
        "indicator": "1.1.1.1", "severity": "critical", "source": "ff_src",
    })
    await insert_entry("ff_src", {
        "indicator": "2.2.2.2", "severity": "low", "source": "ff_src",
    })

    crit = await query_entries(source_name="ff_src", filters={"severity": "critical"})
    assert [r["indicator"] for r in crit] == ["1.1.1.1"]


@pytest.mark.asyncio
async def test_query_entries_unknown_field_filter_is_ignored(temp_data_dir):
    """A non-whitelisted column (extra-JSON field or injection attempt) must be
    silently dropped, never interpolated into SQL — the query still succeeds and
    returns all rows."""
    from backend.db.manager import insert_entry, query_entries

    await insert_entry("ff_src2", {
        "indicator": "3.3.3.3", "severity": "high", "source": "ff_src2",
    })

    # Bogus column name with SQL metacharacters — must not raise and must not
    # filter anything out.
    rows = await query_entries(
        source_name="ff_src2",
        filters={"severity = 'x' OR 1=1; --": "boom", "not_a_column": "v"},
    )
    assert len(rows) == 1
    assert rows[0]["indicator"] == "3.3.3.3"


# ── on-demand field derivation for Raw-table defaults (issue_local_009 rev1) ──


@pytest.mark.asyncio
async def test_get_recently_populated_fields(temp_data_dir):
    """Derives populated field names from recent entries, excluding internal/
    always-shown columns and empty values, ranked by frequency."""
    from backend.db.manager import insert_entry, get_recently_populated_fields

    # Two entries: cve_id populated in both, actor in one, title always empty.
    await insert_entry("fp_src", {
        "source": "fp_src", "indicator": "1.1.1.1", "indicator_type": "ip",
        "cve_id": "CVE-2026-1", "actor": "APT-X", "title": "",
        "published_at": "2026-01-01",
    })
    await insert_entry("fp_src", {
        "source": "fp_src", "indicator": "2.2.2.2", "indicator_type": "ip",
        "cve_id": "CVE-2026-2", "title": "",
        "published_at": "2026-01-02",
    })

    fields = await get_recently_populated_fields()

    assert "cve_id" in fields
    assert "indicator" in fields
    assert "indicator_type" in fields
    assert "actor" in fields
    # Empty / internal / always-shown columns are excluded.
    assert "title" not in fields
    assert "source" not in fields
    assert "ingested_at" not in fields
    assert "extra" not in fields
    # cve_id (2 hits) ranks ahead of actor (1 hit).
    assert fields.index("cve_id") < fields.index("actor")


@pytest.mark.asyncio
async def test_get_recently_populated_fields_empty(temp_data_dir):
    from backend.db.manager import get_recently_populated_fields

    assert await get_recently_populated_fields() == []
