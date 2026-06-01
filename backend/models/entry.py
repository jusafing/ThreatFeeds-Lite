"""Pydantic models for ingest entries and API responses."""
from __future__ import annotations

from typing import Any, Optional
from pydantic import BaseModel, Field


class EntryIn(BaseModel):
    """Payload accepted by the push listener and ingest endpoints."""
    model_config = {"extra": "allow"}  # pass-through unknown fields to normaliser

    indicator: Optional[str] = None
    indicator_type: Optional[str] = None
    threat_type: Optional[str] = None
    severity: Optional[str] = None
    confidence: Optional[float] = None
    source: Optional[str] = None
    source_url: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[str] = None
    tlp: Optional[str] = None
    published_at: Optional[str] = None
    first_seen: Optional[str] = None
    last_seen: Optional[str] = None
    cve_id: Optional[str] = None
    cvss_score: Optional[float] = None
    cvss_vector: Optional[str] = None
    affected_product: Optional[str] = None
    affected_vendor: Optional[str] = None
    patch_available: Optional[bool] = None
    mitre_attack_id: Optional[str] = None
    malware_family: Optional[str] = None
    campaign: Optional[str] = None
    actor: Optional[str] = None
    country: Optional[str] = None
    autonomous_system: Optional[str] = None
    port: Optional[int] = None
    protocol: Optional[str] = None
    geo_lat: Optional[float] = None
    geo_lon: Optional[float] = None
    ingest_mode: Optional[str] = None
    raw: Optional[str] = None


class EntryOut(BaseModel):
    """Entry as returned by the viewer API."""
    model_config = {"extra": "allow"}

    id: Optional[int] = None
    source: str
    ingested_at: str
    indicator: Optional[str] = None
    indicator_type: Optional[str] = None
    threat_type: Optional[str] = None
    severity: Optional[str] = None
    title: Optional[str] = None
    ingest_mode: Optional[str] = None


class SummaryItem(BaseModel):
    source: str
    count: int


class IngestResponse(BaseModel):
    inserted: int
    skipped: int
    errors: list[str] = Field(default_factory=list)
    # New 4-counter summary fields (back-compat: `skipped` == duplicates + discarded)
    total_read: int = 0
    duplicates: int = 0
    discarded: int = 0


class PreviewResponse(BaseModel):
    preview_id: str
    source_name: str
    format: str
    total: int
    sample: list[dict[str, Any]] = Field(default_factory=list)
    expires_in_seconds: int = 300
