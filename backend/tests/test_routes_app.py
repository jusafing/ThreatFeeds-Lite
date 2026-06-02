"""Tests for /api/app/base-prefix (prompts-017)."""
from __future__ import annotations

import pytest
import yaml
from fastapi.testclient import TestClient

from backend.main import app
from backend.config import loader
from backend.api import routes_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Redirect APP_CONFIG_PATH to a tmp file seeded with empty defaults."""
    fake = tmp_path / "application.yaml"
    fake.write_text(yaml.safe_dump({"app_base_prefix": ""}), encoding="utf-8")
    monkeypatch.setattr(loader, "APP_CONFIG_PATH", fake)
    # Redirect branding storage + root into tmp so logo tests never touch the
    # real repo tree.
    monkeypatch.setattr(routes_app, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(routes_app, "_BRANDING_DIR", tmp_path / "data" / "branding")
    return TestClient(app)


def test_get_base_prefix_default(client):
    resp = client.get("/api/app/base-prefix")
    assert resp.status_code == 200
    assert resp.json() == {"app_base_prefix": ""}


def test_put_base_prefix_round_trip(client):
    resp = client.put("/api/app/base-prefix", json={"app_base_prefix": "/feeds"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["app_base_prefix"] == "/feeds"
    assert body["restart_required"] is True
    # Re-read to confirm persisted
    resp2 = client.get("/api/app/base-prefix")
    assert resp2.json() == {"app_base_prefix": "/feeds"}


def test_put_base_prefix_empty_is_valid(client):
    """Empty string is a valid value (means 'no prefix')."""
    # First set to something, then back to empty.
    client.put("/api/app/base-prefix", json={"app_base_prefix": "/x"})
    resp = client.put("/api/app/base-prefix", json={"app_base_prefix": ""})
    assert resp.status_code == 200
    assert resp.json()["app_base_prefix"] == ""


@pytest.mark.parametrize("bad", [
    "feeds",         # no leading slash
    "/feeds/",       # trailing slash
    "/has spaces",   # whitespace
    "http://x",      # scheme
    "/a//b",         # double slash
])
def test_put_base_prefix_rejects_invalid_format(client, bad):
    resp = client.put("/api/app/base-prefix", json={"app_base_prefix": bad})
    assert resp.status_code == 400


def test_put_base_prefix_rejects_non_string(client):
    resp = client.put("/api/app/base-prefix", json={"app_base_prefix": 42})
    assert resp.status_code == 400


def test_put_base_prefix_rejects_bool(client):
    """Booleans must be rejected explicitly (defensive — not a string anyway)."""
    resp = client.put("/api/app/base-prefix", json={"app_base_prefix": True})
    assert resp.status_code == 400


def test_put_base_prefix_rejects_missing_key(client):
    resp = client.put("/api/app/base-prefix", json={})
    assert resp.status_code == 400


# ── pagination_max (prompts-043) ─────────────────────────────────────────────


def test_get_pagination_max_default(client):
    resp = client.get("/api/app/pagination-max")
    assert resp.status_code == 200
    assert resp.json() == {"pagination_max": 1000}


def test_put_pagination_max_round_trip(client):
    resp = client.put("/api/app/pagination-max", json={"pagination_max": 250})
    assert resp.status_code == 200
    assert resp.json() == {"pagination_max": 250}
    # No restart_required on this setting (read live by the viewer).
    assert "restart_required" not in resp.json()
    resp2 = client.get("/api/app/pagination-max")
    assert resp2.json() == {"pagination_max": 250}


@pytest.mark.parametrize("bad", [49, 0, -1, 100_001, 999_999])
def test_put_pagination_max_rejects_out_of_range(client, bad):
    resp = client.put("/api/app/pagination-max", json={"pagination_max": bad})
    assert resp.status_code == 400


def test_put_pagination_max_rejects_non_int(client):
    resp = client.put("/api/app/pagination-max", json={"pagination_max": "100"})
    assert resp.status_code == 400


def test_put_pagination_max_rejects_bool(client):
    resp = client.put("/api/app/pagination-max", json={"pagination_max": True})
    assert resp.status_code == 400


def test_put_pagination_max_rejects_missing_key(client):
    resp = client.put("/api/app/pagination-max", json={})
    assert resp.status_code == 400


# ── watcher_max_events (issue_local_006) ─────────────────────────────────────


def test_get_watcher_max_events_default(client):
    resp = client.get("/api/app/watcher-max-events")
    assert resp.status_code == 200
    assert resp.json() == {"watcher_max_events": 1000}


def test_put_watcher_max_events_round_trip(client):
    resp = client.put("/api/app/watcher-max-events", json={"watcher_max_events": 250})
    assert resp.status_code == 200
    assert resp.json() == {"watcher_max_events": 250}
    assert "restart_required" not in resp.json()
    resp2 = client.get("/api/app/watcher-max-events")
    assert resp2.json() == {"watcher_max_events": 250}


@pytest.mark.parametrize("bad", [9, 0, -1, 100_001, 999_999])
def test_put_watcher_max_events_rejects_out_of_range(client, bad):
    resp = client.put("/api/app/watcher-max-events", json={"watcher_max_events": bad})
    assert resp.status_code == 400


def test_put_watcher_max_events_rejects_non_int(client):
    resp = client.put("/api/app/watcher-max-events", json={"watcher_max_events": "100"})
    assert resp.status_code == 400


def test_put_watcher_max_events_rejects_bool(client):
    resp = client.put("/api/app/watcher-max-events", json={"watcher_max_events": True})
    assert resp.status_code == 400


def test_put_watcher_max_events_rejects_missing_key(client):
    resp = client.put("/api/app/watcher-max-events", json={})
    assert resp.status_code == 400


# ── branding logo (prompts-045) ──────────────────────────────────────────────
# 1x1 transparent PNG.
_PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6360000002000154a24f3f0000000049454e44ae42"
    "6082"
)


def test_logo_info_default_is_false(client):
    resp = client.get("/api/app/logo-info")
    assert resp.status_code == 200
    assert resp.json() == {"has_logo": False}


def test_get_logo_404_when_unset(client):
    resp = client.get("/api/app/logo")
    assert resp.status_code == 404


def test_upload_logo_round_trip(client):
    resp = client.post(
        "/api/app/logo",
        files={"file": ("logo.png", _PNG_BYTES, "image/png")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_logo"] is True
    assert body["logo_path"] == "data/branding/logo.png"

    # logo-info now reports True.
    assert client.get("/api/app/logo-info").json() == {"has_logo": True}

    # GET serves the bytes with nosniff.
    got = client.get("/api/app/logo")
    assert got.status_code == 200
    assert got.content == _PNG_BYTES
    assert got.headers["x-content-type-options"] == "nosniff"
    assert got.headers["content-type"] == "image/png"


def test_upload_logo_rejects_svg(client):
    resp = client.post(
        "/api/app/logo",
        files={"file": ("x.svg", b"<svg onload=alert(1)></svg>", "image/svg+xml")},
    )
    assert resp.status_code == 400


def test_upload_logo_rejects_content_type_lie(client):
    """A PNG Content-Type over non-image bytes must be rejected by magic-byte sniff."""
    resp = client.post(
        "/api/app/logo",
        files={"file": ("logo.png", b"this is definitely not an image", "image/png")},
    )
    assert resp.status_code == 400


def test_upload_logo_rejects_svg_disguised_as_png(client):
    """SVG bytes mislabelled as image/png must still be rejected (stored-XSS guard)."""
    resp = client.post(
        "/api/app/logo",
        files={"file": ("logo.png", b"<svg onload=alert(1)></svg>", "image/png")},
    )
    assert resp.status_code == 400


def test_upload_logo_rejects_empty(client):
    resp = client.post(
        "/api/app/logo",
        files={"file": ("empty.png", b"", "image/png")},
    )
    assert resp.status_code == 400


def test_upload_logo_rejects_oversize(client):
    big = b"\x00" * (2 * 1024 * 1024 + 1)
    resp = client.post(
        "/api/app/logo",
        files={"file": ("big.png", big, "image/png")},
    )
    assert resp.status_code == 413


def test_upload_logo_replaces_previous_format(client, tmp_path):
    # Upload a PNG, then a GIF — the PNG file must be gone afterwards.
    client.post("/api/app/logo", files={"file": ("logo.png", _PNG_BYTES, "image/png")})
    gif = b"GIF89a" + b"\x00" * 20
    resp = client.post("/api/app/logo", files={"file": ("logo.gif", gif, "image/gif")})
    assert resp.status_code == 200
    assert resp.json()["logo_path"] == "data/branding/logo.gif"
    branding = tmp_path / "data" / "branding"
    names = sorted(p.name for p in branding.glob("logo.*"))
    assert names == ["logo.gif"]


def test_delete_logo(client):
    client.post("/api/app/logo", files={"file": ("logo.png", _PNG_BYTES, "image/png")})
    resp = client.delete("/api/app/logo")
    assert resp.status_code == 200
    assert resp.json() == {"has_logo": False}
    assert client.get("/api/app/logo").status_code == 404
