"""Pydantic models for the Watchers feature (issue_local_006)."""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class WatcherCondition(BaseModel):
    """One field-match condition. ``field`` empty/'*' means match any field."""
    field: str = ""
    value: str
    match_type: str = "exact"  # exact | wildcard | regex | gte | lte


class WatcherIn(BaseModel):
    """Payload accepted when creating or updating a watcher."""
    model_config = {"extra": "ignore"}

    name: str
    severity: str = "low"            # low | medium | high | critical
    dataset: str = "all"             # all | raw | normalized
    feeds: list[str] = Field(default_factory=list)
    conditions: list[WatcherCondition] = Field(default_factory=list)
    mode: str = "realtime"           # realtime | scheduled
    interval_sec: int = 120
    format: str = "json"             # json | csv | xml
    max_feed_events: int = 10
    enabled: bool = False
    publish_target: str = "local"    # local | webhook | http
    webhook_url: Optional[str] = None
    auth_header: Optional[str] = None
    auth_value: Optional[str] = None


class WatcherEnabledIn(BaseModel):
    """Payload for the enable/disable toggle endpoint."""
    enabled: bool


class WatcherOut(BaseModel):
    """A watcher definition as returned by the API."""
    model_config = {"extra": "allow"}

    id: str
    name: str
    severity: str
    dataset: str
    feeds: list[str]
    conditions: list[dict[str, Any]]
    mode: str
    interval_sec: int
    format: str
    max_feed_events: int
    enabled: bool
    trigger_count: int
    created_at: str
    updated_at: str
    last_triggered_at: Optional[str] = None
    publish_target: str = "local"
    webhook_url: Optional[str] = None
    auth_header: Optional[str] = None
    auth_value: Optional[str] = None
    delivery_error_count: int = 0
    last_delivery_error: Optional[str] = None


class WatcherEvent(BaseModel):
    """A triggered event row as returned by the API."""
    id: int
    watcher_id: str
    dataset: str
    source_entry_id: int
    source_name: Optional[str] = None
    triggered_at: str
    event: dict[str, Any]
    delivery_status: Optional[str] = None
    delivery_error: Optional[str] = None
    delivered_at: Optional[str] = None
