"""
DB schema — defines the entries table.
One SQLite database file per source stored in data/<source_name>.db.
"""
from __future__ import annotations

# DDL for the entries table.
# Dynamic columns are stored in a JSON blob (`extra`) so that adding custom
# fields does not require schema migrations.

CREATE_ENTRIES_TABLE = """
CREATE TABLE IF NOT EXISTS entries (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    -- Core identification
    indicator          TEXT,
    indicator_type     TEXT,
    threat_type        TEXT,
    severity           TEXT,
    confidence         REAL,
    source             TEXT NOT NULL,
    source_url         TEXT,
    title              TEXT,
    description        TEXT,
    tags               TEXT,
    tlp                TEXT,
    -- Timestamps
    published_at       TEXT,
    first_seen         TEXT,
    last_seen          TEXT,
    ingested_at        TEXT NOT NULL,
    -- Vulnerability fields
    cve_id             TEXT,
    cvss_score         REAL,
    cvss_vector        TEXT,
    affected_product   TEXT,
    affected_vendor    TEXT,
    patch_available    INTEGER,           -- 0/1 boolean
    -- Threat actor / campaign
    mitre_attack_id    TEXT,
    malware_family     TEXT,
    campaign           TEXT,
    actor              TEXT,
    -- Geo / network
    country            TEXT,
    autonomous_system  TEXT,
    port               INTEGER,
    protocol           TEXT,
    geo_lat            REAL,
    geo_lon            REAL,
    -- Ingest metadata
    ingest_mode        TEXT,
    raw                TEXT,
    -- Normalizer flag (set to 1 after this entry is processed by the normalizer)
    normalized         INTEGER DEFAULT 0,
    -- Custom fields stored as JSON blob
    extra              TEXT DEFAULT '{}'
);
"""

CREATE_DEDUP_INDEX = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_dedup
ON entries (source, indicator, published_at);
"""

CREATE_DEDUP_KEY_INDEX = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_dedup_key
ON entries (dedup_key);
"""

ALTER_ADD_NORMALIZED = "ALTER TABLE entries ADD COLUMN normalized INTEGER DEFAULT 0"
ALTER_ADD_DEDUP_KEY = "ALTER TABLE entries ADD COLUMN dedup_key TEXT"
