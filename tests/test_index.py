from fastapi.testclient import TestClient

from app.main import app
from app.session import create_session

client = TestClient(app)


def _logged_in_cookie():
    session_id = create_session("cid", "secret", "loader")
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
