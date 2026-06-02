"""Tests for the normalizer engine (auto and manual mapping modes)."""
from __future__ import annotations

import json
import pytest


# ── map_entry_auto ─────────────────────────────────────────────────────────────

def test_auto_maps_ip_field():
    from backend.normalizer.engine import map_entry_auto
    result = map_entry_auto({"ip": "1.2.3.4", "severity": "high"}, "test")
    assert result["indicator"] == "1.2.3.4"
    assert result["indicator_type"] == "ip"
    assert result["severity"] == "high"
    assert result["source_name"] == "test"


def test_auto_maps_indicator_canonical_passthrough():
    """Raw `indicator` (the canonical itself) passes through but does NOT
    auto-set indicator_type — the field name does not reveal the type."""
    from backend.normalizer.engine import map_entry_auto
    result = map_entry_auto({"indicator": "evil.com"}, "test")
    assert result["indicator"] == "evil.com"
    assert "indicator_type" not in result


def test_auto_maps_cve():
    from backend.normalizer.engine import map_entry_auto
    result = map_entry_auto({"cve_id": "CVE-2023-1234", "actor": "APT28"}, "test")
    assert result["cve_id"] == "CVE-2023-1234"
    assert result["actor"] == "APT28"


def test_auto_places_unknown_in_extra_norm():
    from backend.normalizer.engine import map_entry_auto
    result = map_entry_auto({"foo_bar": "baz", "ip": "1.2.3.4"}, "test")
    assert "foo_bar" not in result
    extra = json.loads(result.get("extra_norm", "{}"))
    assert extra.get("foo_bar") == "baz"


def test_auto_skips_empty_values():
    from backend.normalizer.engine import map_entry_auto
    result = map_entry_auto({"ip": "", "domain": None, "severity": "low"}, "test")
    assert "indicator" not in result
    assert result["severity"] == "low"


def test_auto_no_duplicate_canonical():
    """If two raw fields map to the same canonical, first one wins."""
    from backend.normalizer.engine import map_entry_auto
    result = map_entry_auto({"ip": "1.2.3.4", "ip_address": "9.9.9.9"}, "test")
    # both → `indicator`; whichever Python iterates first wins, second goes to extra
    assert result["indicator"] in ("1.2.3.4", "9.9.9.9")


# ── wildcard synonyms (prompts-021C; canonical names from 021E-pre) ───────────

def _clear_resolver_cache():
    from backend.normalizer.engine import _resolve_canonical
    _resolve_canonical.cache_clear()


def test_auto_wildcard_matches_src_ip_variants():
    _clear_resolver_cache()
    from backend.normalizer.engine import map_entry_auto
    r = map_entry_auto({"src_ip_addr": "1.2.3.4"}, "t")
    assert r["indicator"] == "1.2.3.4"
    assert r["indicator_type"] == "ip"
    r = map_entry_auto({"source_addr_v4": "5.6.7.8"}, "t")
    assert r["indicator"] == "5.6.7.8"
    assert r["indicator_type"] == "ip"


def test_auto_wildcard_matches_cve_variants():
    _clear_resolver_cache()
    from backend.normalizer.engine import map_entry_auto
    r = map_entry_auto({"nvd_cve_id": "CVE-2024-0001"}, "t")
    assert r["cve_id"] == "CVE-2024-0001"
    r = map_entry_auto({"vulnerability.id": "CVE-2024-0002"}, "t")
    assert r["cve_id"] == "CVE-2024-0002"
    r = map_entry_auto({"vuln_identifier": "CVE-2024-0003"}, "t")
    assert r["cve_id"] == "CVE-2024-0003"


def test_auto_wildcard_matches_hash_variants():
    _clear_resolver_cache()
    from backend.normalizer.engine import map_entry_auto
    r = map_entry_auto({"file_md5": "abc"}, "t")
    assert r["indicator"] == "abc"
    assert r["indicator_type"] == "hash_md5"
    r = map_entry_auto({"sha256_hex": "def"}, "t")
    assert r["indicator"] == "def"
    assert r["indicator_type"] == "hash_sha256"


def test_auto_wildcard_matches_timestamp_variants():
    _clear_resolver_cache()
    from backend.normalizer.engine import map_entry_auto
    r = map_entry_auto({"record_published_at": "2024-01-01"}, "t")
    assert r["published_at"] == "2024-01-01"
    r = map_entry_auto({"first_seen_utc": "2024-02-02"}, "t")
    assert r["first_seen"] == "2024-02-02"


def test_auto_exact_match_beats_wildcard():
    """Exact-table match must win even when a wildcard could also match."""
    _clear_resolver_cache()
    from backend.normalizer.engine import _resolve_canonical
    # `ip` is exact → indicator (+ type hint)
    canonical, hint = _resolve_canonical("ip")
    assert canonical == "indicator"
    assert hint == "ip"
    # `hash` is exact → indicator (+ generic hash hint)
    canonical, hint = _resolve_canonical("hash")
    assert canonical == "indicator"
    assert hint == "hash"


def test_auto_existing_ioc_synonyms_unchanged():
    """Regression guard: pre-021C exact synonyms still resolve to indicator."""
    _clear_resolver_cache()
    from backend.normalizer.engine import map_entry_auto
    r = map_entry_auto({"indicator": "evil.com", "src_ip": "1.2.3.4"}, "t")
    # Both → `indicator`; first-wins de-dupe sends the second to extra_norm.
    assert r["indicator"] in ("evil.com", "1.2.3.4")


def test_auto_manual_mode_unaffected_by_wildcards():
    """Manual mode must not consult wildcard table — only explicit mappings."""
    _clear_resolver_cache()
    from backend.normalizer.engine import map_entry_manual
    # `src_ip_addr` would match `*src*ip*` wildcard in auto mode, but in manual
    # mode without an explicit mapping it must fall through to extra_norm.
    r = map_entry_manual({"src_ip_addr": "1.2.3.4"}, "t", mappings={})
    assert "indicator" not in r
    extra = json.loads(r.get("extra_norm", "{}"))
    assert extra.get("src_ip_addr") == "1.2.3.4"


# ── map_entry_manual ──────────────────────────────────────────────────────────

def test_manual_explicit_mapping():
    from backend.normalizer.engine import map_entry_manual
    mappings = {"src_ip": "indicator", "threat": "severity"}
    result = map_entry_manual({"src_ip": "1.2.3.4", "threat": "critical"}, "test", mappings)
    assert result["indicator"] == "1.2.3.4"
    assert result["severity"] == "critical"


def test_manual_unmapped_go_to_extra():
    from backend.normalizer.engine import map_entry_manual
    mappings = {"src_ip": "indicator"}
    result = map_entry_manual({"src_ip": "1.2.3.4", "unknown_col": "value"}, "test", mappings)
    extra = json.loads(result.get("extra_norm", "{}"))
    assert extra.get("unknown_col") == "value"


# ── run_normalizer (mocked DB) ────────────────────────────────────────────────

@pytest.mark.anyio
async def test_run_normalizer_disabled():
    from unittest.mock import patch
    with patch("backend.normalizer.engine.load_normalizer_config", return_value={"enabled": False}):
        from backend.normalizer.engine import run_normalizer
        result = await run_normalizer()
    assert result["status"] == "disabled"
    assert result["processed"] == 0


@pytest.mark.anyio
async def test_run_normalizer_auto_empty():
    """When no un-normalized entries exist, run completes with 0 processed."""
    from unittest.mock import patch, AsyncMock
    with patch("backend.normalizer.engine.query_entries", new=AsyncMock(return_value=[])), \
         patch("backend.normalizer.engine.load_normalizer_config",
               return_value={"enabled": True, "mode": "auto", "manual_mappings": {}}):
        from backend.normalizer.engine import run_normalizer
        result = await run_normalizer()
    assert result["status"] == "ok"
    assert result["processed"] == 0


# ── normalizer config fallback ─────────────────────────────────────────────────


def test_load_normalizer_config_missing_file_returns_defaults(tmp_path, monkeypatch):
    """When the YAML file is missing, defaults are returned and no exception is raised."""
    import backend.normalizer.config as cfg_mod
    monkeypatch.setattr(cfg_mod, "_NORMALIZER_CONFIG_PATH", tmp_path / "missing.yaml")
    result = cfg_mod.load_normalizer_config()
    assert result["mode"] == "auto"
    assert result["enabled"] is True
    assert result["interval_minutes"] == 10  # prompts-021A: default changed 30 → 10
    assert result["manual_mappings"] == {}


def test_load_normalizer_config_partial_yaml_merges_defaults(tmp_path, monkeypatch):
    """A partial YAML is merged over defaults so all keys are present."""
    import backend.normalizer.config as cfg_mod
    p = tmp_path / "partial.yaml"
    p.write_text("mode: manual\n")
    monkeypatch.setattr(cfg_mod, "_NORMALIZER_CONFIG_PATH", p)
    result = cfg_mod.load_normalizer_config()
    assert result["mode"] == "manual"
    assert result["enabled"] is True        # default preserved
    assert result["interval_minutes"] == 10  # default preserved (prompts-021A: 30 → 10)


# ── prompts-021F: active mapping_version threading ────────────────────────────

@pytest.mark.anyio
async def test_run_normalizer_threads_active_mapping_version_id(tmp_path, monkeypatch):
    """When an active mapping_version exists for a source, run_normalizer:
       1. uses that version's mapping (not yaml manual_mappings),
       2. writes mapping_version_id into normalized_entries,
       3. ignores yaml manual_mappings even when both are defined.
    """
    from unittest.mock import AsyncMock, patch

    import backend.normalizer.db as ndb
    import backend.normalizer.mappings as mappings_mod
    from backend.normalizer.mappings import activate_version, create_version

    monkeypatch.setattr(ndb, "_NORM_DB_PATH", tmp_path / "normalized.db")
    monkeypatch.setattr(
        mappings_mod, "_MAPPINGS_DB_PATH", tmp_path / "mapping_versions.db",
    )

    # Active version maps `vendor_field` → indicator. Yaml mapping (which
    # would route `vendor_field` → severity) MUST be ignored when an active
    # version exists.
    vid = await create_version(
        source_name="feed-x",
        mapping={"vendor_field": "indicator"},
        origin="proposal",
    )
    await activate_version(vid)

    fake_entries = [
        {"id": 101, "source": "feed-x", "vendor_field": "1.2.3.4"},
    ]

    async def _noop_mark(src, ids):
        return None

    with patch(
        "backend.normalizer.engine.query_entries",
        new=AsyncMock(return_value=fake_entries),
    ), patch(
        "backend.normalizer.engine.mark_normalized", new=AsyncMock(side_effect=_noop_mark),
    ), patch(
        "backend.normalizer.engine.load_normalizer_config",
        return_value={
            "enabled": True,
            "mode": "manual",
            # Intentionally divergent yaml — must be ignored in favour of
            # the active version.
            "manual_mappings": {"feed-x": {"vendor_field": "severity"}},
        },
    ):
        from backend.normalizer.engine import run_normalizer
        result = await run_normalizer()

    assert result["inserted"] == 1

    rows = await ndb.query_normalized(source_name="feed-x")
    assert len(rows) == 1
    row = rows[0]
    assert row["mapping_version_id"] == vid
    # active version's mapping wins → indicator, not severity.
    assert row["indicator"] == "1.2.3.4"
    assert row.get("severity") is None


@pytest.mark.anyio
async def test_run_normalizer_falls_back_to_yaml_when_no_active_version(tmp_path, monkeypatch):
    """When no active mapping_version exists, yaml manual_mappings is used
    and mapping_version_id stays NULL (legacy path)."""
    from unittest.mock import AsyncMock, patch

    import backend.normalizer.db as ndb
    import backend.normalizer.mappings as mappings_mod

    monkeypatch.setattr(ndb, "_NORM_DB_PATH", tmp_path / "normalized.db")
    monkeypatch.setattr(
        mappings_mod, "_MAPPINGS_DB_PATH", tmp_path / "mapping_versions.db",
    )

    fake_entries = [
        {"id": 202, "source": "feed-y", "vendor_field": "9.9.9.9"},
    ]

    async def _noop_mark(src, ids):
        return None

    with patch(
        "backend.normalizer.engine.query_entries",
        new=AsyncMock(return_value=fake_entries),
    ), patch(
        "backend.normalizer.engine.mark_normalized", new=AsyncMock(side_effect=_noop_mark),
    ), patch(
        "backend.normalizer.engine.load_normalizer_config",
        return_value={
            "enabled": True,
            "mode": "manual",
            "manual_mappings": {"feed-y": {"vendor_field": "indicator"}},
        },
    ):
        from backend.normalizer.engine import run_normalizer
        result = await run_normalizer()

    assert result["inserted"] == 1
    rows = await ndb.query_normalized(source_name="feed-y")
    assert len(rows) == 1
    assert rows[0]["mapping_version_id"] is None
    assert rows[0]["indicator"] == "9.9.9.9"


@pytest.mark.anyio
async def test_run_normalizer_auto_mode_skips_active_version(tmp_path, monkeypatch):
    """Auto mode does NOT consult mapping_versions; rows carry
    mapping_version_id=NULL even when an active version exists."""
    from unittest.mock import AsyncMock, patch

    import backend.normalizer.db as ndb
    import backend.normalizer.mappings as mappings_mod
    from backend.normalizer.mappings import activate_version, create_version

    monkeypatch.setattr(ndb, "_NORM_DB_PATH", tmp_path / "normalized.db")
    monkeypatch.setattr(
        mappings_mod, "_MAPPINGS_DB_PATH", tmp_path / "mapping_versions.db",
    )

    vid = await create_version(
        source_name="feed-z",
        mapping={"vendor_field": "indicator"},
        origin="proposal",
    )
    await activate_version(vid)

    fake_entries = [
        {"id": 303, "source": "feed-z", "ip": "5.5.5.5"},  # auto-resolves via synonyms
    ]

    async def _noop_mark(src, ids):
        return None

    with patch(
        "backend.normalizer.engine.query_entries",
        new=AsyncMock(return_value=fake_entries),
    ), patch(
        "backend.normalizer.engine.mark_normalized", new=AsyncMock(side_effect=_noop_mark),
    ), patch(
        "backend.normalizer.engine.load_normalizer_config",
        return_value={"enabled": True, "mode": "auto", "manual_mappings": {}},
    ):
        from backend.normalizer.engine import run_normalizer
        await run_normalizer()

    rows = await ndb.query_normalized(source_name="feed-z")
    assert len(rows) == 1
    assert rows[0]["mapping_version_id"] is None
    assert rows[0]["indicator"] == "5.5.5.5"


# ── prompts-032 Phase E: smart mode (consolidated mapping) ────────────────────

@pytest.mark.anyio
async def test_run_normalizer_smart_applies_active_consolidated(tmp_path, monkeypatch):
    """mode='smart' with an active consolidated mapping applies that one global
    {raw_field: canonical} dict to entries from EVERY source. Rows carry
    mapping_version_id=NULL and the run reports no warning."""
    from unittest.mock import AsyncMock, patch

    import backend.normalizer.db as ndb

    monkeypatch.setattr(ndb, "_NORM_DB_PATH", tmp_path / "normalized.db")

    # vendor_field would NOT auto-resolve, proving the consolidated map is used.
    fake_entries = [
        {"id": 401, "source": "feed-a", "vendor_field": "1.1.1.1"},
        {"id": 402, "source": "feed-b", "vendor_field": "2.2.2.2"},
    ]

    async def _noop_mark(src, ids):
        return None

    active_consolidated = {
        "id": 7,
        "mapping": {"vendor_field": "indicator"},
        "sources": ["feed-a", "feed-b"],
        "active": True,
    }

    with patch(
        "backend.normalizer.engine.query_entries",
        new=AsyncMock(return_value=fake_entries),
    ), patch(
        "backend.normalizer.engine.mark_normalized", new=AsyncMock(side_effect=_noop_mark),
    ), patch(
        "backend.normalizer.engine.get_active_consolidated",
        new=AsyncMock(return_value=active_consolidated),
    ), patch(
        "backend.normalizer.engine.load_normalizer_config",
        return_value={"enabled": True, "mode": "smart", "manual_mappings": {}},
    ):
        from backend.normalizer.engine import run_normalizer
        result = await run_normalizer()

    assert result["status"] == "ok"
    assert result["mode"] == "smart"
    assert result["inserted"] == 2
    assert result["warning"] is None

    rows_a = await ndb.query_normalized(source_name="feed-a")
    rows_b = await ndb.query_normalized(source_name="feed-b")
    assert rows_a[0]["indicator"] == "1.1.1.1"
    assert rows_a[0]["mapping_version_id"] is None
    assert rows_b[0]["indicator"] == "2.2.2.2"


@pytest.mark.anyio
async def test_run_normalizer_smart_without_active_falls_back_to_auto(tmp_path, monkeypatch):
    """mode='smart' with NO active consolidated mapping falls back to auto
    resolution and returns a non-null warning for the UI banner (Q5)."""
    from unittest.mock import AsyncMock, patch

    import backend.normalizer.db as ndb

    monkeypatch.setattr(ndb, "_NORM_DB_PATH", tmp_path / "normalized.db")

    # `ip` auto-resolves; vendor_field does not (would only map under a
    # consolidated mapping, which is absent here).
    fake_entries = [
        {"id": 501, "source": "feed-c", "ip": "3.3.3.3", "vendor_field": "x"},
    ]

    async def _noop_mark(src, ids):
        return None

    with patch(
        "backend.normalizer.engine.query_entries",
        new=AsyncMock(return_value=fake_entries),
    ), patch(
        "backend.normalizer.engine.mark_normalized", new=AsyncMock(side_effect=_noop_mark),
    ), patch(
        "backend.normalizer.engine.get_active_consolidated",
        new=AsyncMock(return_value=None),
    ), patch(
        "backend.normalizer.engine.load_normalizer_config",
        return_value={"enabled": True, "mode": "smart", "manual_mappings": {}},
    ):
        from backend.normalizer.engine import run_normalizer
        result = await run_normalizer()

    assert result["status"] == "ok"
    assert result["inserted"] == 1
    assert result["warning"] is not None
    assert "consolidated" in result["warning"].lower()

    rows = await ndb.query_normalized(source_name="feed-c")
    # auto-resolution applied: ip → indicator; vendor_field → extra_norm,
    # which query_normalized merges back to a top-level key.
    assert rows[0]["indicator"] == "3.3.3.3"
    assert rows[0].get("vendor_field") == "x"


@pytest.mark.anyio
async def test_run_normalizer_records_run_history(tmp_path, monkeypatch):
    """prompts-039: a smart run with an active consolidated mapping records one
    run-history row carrying the trigger, mode, counters, and proposal
    provenance (name resolved via get_proposal)."""
    from unittest.mock import AsyncMock, patch

    import backend.normalizer.db as ndb
    from backend.normalizer.run_history import list_runs

    monkeypatch.setattr(ndb, "_NORM_DB_PATH", tmp_path / "normalized.db")

    fake_entries = [
        {"id": 601, "source": "feed-a", "vendor_field": "1.1.1.1"},
    ]

    async def _noop_mark(src, ids):
        return None

    active_consolidated = {
        "id": 9,
        "mapping": {"vendor_field": "indicator"},
        "sources": ["feed-a"],
        "active": True,
        "proposal_id": 77,
    }

    with patch(
        "backend.normalizer.engine.query_entries",
        new=AsyncMock(return_value=fake_entries),
    ), patch(
        "backend.normalizer.engine.mark_normalized", new=AsyncMock(side_effect=_noop_mark),
    ), patch(
        "backend.normalizer.engine.get_active_consolidated",
        new=AsyncMock(return_value=active_consolidated),
    ), patch(
        "backend.normalizer.engine.get_proposal",
        new=AsyncMock(return_value={"proposal_name": "Proposal-X"}),
    ), patch(
        "backend.normalizer.engine.load_normalizer_config",
        return_value={"enabled": True, "mode": "smart", "manual_mappings": {}},
    ):
        from backend.normalizer.engine import run_normalizer
        await run_normalizer(trigger="reapply")

    runs = await list_runs()
    assert len(runs) == 1
    row = runs[0]
    assert row["trigger"] == "reapply"
    assert row["mode"] == "smart"
    assert row["status"] == "ok"
    assert row["inserted"] == 1
    assert row["proposal_id"] == 77
    assert row["proposal_name"] == "Proposal-X"
    assert row["sources"] == ["feed-a"]


@pytest.mark.anyio
async def test_run_normalizer_disabled_records_no_history(monkeypatch):
    """A disabled run returns early and must NOT write a history row."""
    from unittest.mock import patch

    from backend.normalizer.run_history import list_runs

    with patch(
        "backend.normalizer.engine.load_normalizer_config",
        return_value={"enabled": False},
    ):
        from backend.normalizer.engine import run_normalizer
        result = await run_normalizer()

    assert result["status"] == "disabled"
    assert await list_runs() == []


# ── issue_local_02: normalized field filters ──────────────────────────────────

@pytest.mark.anyio
async def test_query_normalized_field_filter_matches_validated_column(
    tmp_path, monkeypatch,
):
    """An arbitrary filter on a yaml-derived column returns only matching rows;
    an unknown/unsafe column name is silently dropped (never interpolated into
    SQL)."""
    import backend.normalizer.db as ndb

    monkeypatch.setattr(ndb, "_NORM_DB_PATH", tmp_path / "normalized.db")

    await ndb.insert_normalized({
        "source_entry_id": 1, "source_name": "feed-q",
        "indicator": "1.1.1.1", "severity": "critical",
    })
    await ndb.insert_normalized({
        "source_entry_id": 2, "source_name": "feed-q",
        "indicator": "2.2.2.2", "severity": "low",
    })

    crit = await ndb.query_normalized(
        source_name="feed-q", filters={"severity": "critical"},
    )
    assert [r["indicator"] for r in crit] == ["1.1.1.1"]

    # Unknown column + injection attempt are dropped → all rows returned.
    everything = await ndb.query_normalized(
        source_name="feed-q",
        filters={"not_a_column": "x", "severity = 'x'; DROP TABLE": "y"},
    )
    assert len(everything) == 2
