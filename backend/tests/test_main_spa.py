"""Tests for the SPA catch-all + base-prefix injection.

Originally added in prompts-017; updated in prompts-019 for the new
no-prefix-relative contract:

    prefix == ""   →  inject <base href="./">; OMIT the app-base-prefix <meta>
    prefix != ""   →  inject <base href="<prefix>/"> AND the <meta> tag
"""
from __future__ import annotations

import pytest
import yaml
from fastapi.testclient import TestClient

from backend.config import loader


@pytest.fixture
def client_with_prefix(tmp_path, monkeypatch):
    """TestClient bound to a tmp application.yaml so we can set the prefix."""
    fake = tmp_path / "application.yaml"
    fake.write_text(yaml.safe_dump({"app_base_prefix": "/feeds"}), encoding="utf-8")
    monkeypatch.setattr(loader, "APP_CONFIG_PATH", fake)
    # The catch-all reads load_app_base_prefix() per request, so we can use
    # the existing app without reloading the module.
    from backend.main import app
    return TestClient(app)


@pytest.fixture
def client_empty_prefix(tmp_path, monkeypatch):
    fake = tmp_path / "application.yaml"
    fake.write_text(yaml.safe_dump({"app_base_prefix": ""}), encoding="utf-8")
    monkeypatch.setattr(loader, "APP_CONFIG_PATH", fake)
    from backend.main import app
    return TestClient(app)


def _frontend_dist_present() -> bool:
    from backend.main import _FRONTEND_DIST
    return (_FRONTEND_DIST / "index.html").exists()


@pytest.mark.skipif(not _frontend_dist_present(), reason="frontend/dist not built")
def test_spa_index_has_meta_and_base_with_prefix(client_with_prefix):
    """Non-empty prefix → both <base href="/feeds/"> and the meta tag present."""
    resp = client_with_prefix.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    body = resp.text
    assert '<meta name="app-base-prefix" content="/feeds">' in body
    assert '<base href="/feeds/">' in body


@pytest.mark.skipif(not _frontend_dist_present(), reason="frontend/dist not built")
def test_spa_index_empty_prefix_omits_meta_and_uses_relative_base(client_empty_prefix):
    """Empty prefix → <base href="./"> present, app-base-prefix <meta> ABSENT."""
    resp = client_empty_prefix.get("/")
    assert resp.status_code == 200
    body = resp.text
    assert '<base href="./">' in body
    # The contract is "no prefix machinery visible in the document".
    assert 'name="app-base-prefix"' not in body


@pytest.mark.skipif(not _frontend_dist_present(), reason="frontend/dist not built")
def test_spa_catch_all_serves_index_for_deep_link(client_empty_prefix):
    """A SPA deep-link path returns the index.html shell, not 404."""
    resp = client_empty_prefix.get("/viewer")
    assert resp.status_code == 200
    # In the empty-prefix case we expect the <base href="./"> marker.
    assert '<base href="./">' in resp.text


@pytest.mark.skipif(not _frontend_dist_present(), reason="frontend/dist not built")
def test_spa_catch_all_does_not_swallow_api_paths(client_empty_prefix):
    """API paths must still be routed normally even when catch-all exists."""
    resp = client_empty_prefix.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.skipif(not _frontend_dist_present(), reason="frontend/dist not built")
def test_spa_injection_is_idempotent_across_prefix_changes():
    """Re-rendering at different prefixes leaves exactly one <base>/<meta>.

    Also checks that switching from a non-empty prefix to an empty prefix
    correctly REMOVES the prior <meta> tag.
    """
    from backend.main import _render_index_html
    once = _render_index_html("/a")
    assert once.count("<base href=") == 1
    assert once.count('name="app-base-prefix"') == 1
    assert '<base href="/a/">' in once

    twice = _render_index_html("/b")
    assert twice.count("<base href=") == 1
    assert twice.count('name="app-base-prefix"') == 1
    assert '<base href="/b/">' in twice
    assert '<base href="/a/">' not in twice

    # Going back to empty must drop the meta tag entirely.
    thrice = _render_index_html("")
    assert thrice.count("<base href=") == 1
    assert '<base href="./">' in thrice
    assert 'name="app-base-prefix"' not in thrice


@pytest.mark.skipif(not _frontend_dist_present(), reason="frontend/dist not built")
def test_spa_index_empty_prefix_has_no_meta_after_repeated_renders():
    """Regression: idempotency must not silently re-introduce the meta tag."""
    from backend.main import _render_index_html
    for _ in range(3):
        out = _render_index_html("")
        assert '<base href="./">' in out
        assert 'name="app-base-prefix"' not in out
