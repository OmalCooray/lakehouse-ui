from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app

client = TestClient(app)


def _logged_in_cookie():
    session_id = main_module.create_session("cid", "secret", "loader")
    return {"lakehouse_session": session_id}


def test_index_serves_the_app_shell_when_authenticated():
    response = client.get("/", cookies=_logged_in_cookie())

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert 'id="editor-container"' in response.text
    assert 'id="worksheet-tabs"' in response.text
    assert 'id="catalog-tree"' in response.text
    assert 'id="history-list"' in response.text
    assert 'id="whoami-text"' in response.text


def test_static_assets_are_served():
    response = client.get("/static/app.css")
    assert response.status_code == 200


def test_static_assets_are_not_heuristically_cached(monkeypatch):
    # Real bug, found live (2026-09-14): StaticFiles sends ETag/Last-Modified
    # but no Cache-Control, so browsers fall back to heuristic freshness and
    # can keep running pre-redeploy JS indefinitely with no revalidation —
    # confirmed with a fresh browser tab still executing an old app.js well
    # after a new image had already rolled out. `no-cache` (not `no-store`)
    # still allows revalidation via the existing ETag.
    response = client.get("/static/app.js")
    assert response.headers["cache-control"] == "no-cache"


def test_non_static_responses_are_unaffected_by_the_cache_header(monkeypatch):
    response = client.get("/healthz")
    assert "cache-control" not in {k.lower() for k in response.headers}
