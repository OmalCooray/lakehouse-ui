import app.main as main_module
from fastapi.testclient import TestClient

from app.main import app
from app.polaris_auth import LoginError
from app.session import get_session

client = TestClient(app)


def test_login_success_sets_cookie_and_returns_principal(monkeypatch):
    monkeypatch.setenv("POLARIS_ENDPOINT", "http://polaris:8181/api/catalog")
    monkeypatch.setattr(main_module, "polaris_login", lambda endpoint, cid, secret: "loader")

    response = client.post("/login", json={"client_id": "cid", "client_secret": "secret"})

    assert response.status_code == 200
    assert response.json() == {"principal": "loader"}
    cookie = response.cookies.get("lakehouse_session")
    assert cookie is not None
    assert get_session(cookie).principal_name == "loader"


def test_login_failure_returns_401_and_sets_no_cookie(monkeypatch):
    monkeypatch.setenv("POLARIS_ENDPOINT", "http://polaris:8181/api/catalog")

    def raise_login_error(endpoint, cid, secret):
        raise LoginError("invalid client_id or client_secret")

    monkeypatch.setattr(main_module, "polaris_login", raise_login_error)

    response = client.post("/login", json={"client_id": "cid", "client_secret": "wrong"})

    assert response.status_code == 401
    assert "invalid" in response.json()["detail"]
    assert response.cookies.get("lakehouse_session") is None


def test_logout_clears_the_session(monkeypatch):
    monkeypatch.setattr(main_module, "polaris_login", lambda endpoint, cid, secret: "loader")
    login_response = client.post("/login", json={"client_id": "cid", "client_secret": "secret"})
    cookie = login_response.cookies.get("lakehouse_session")

    response = client.post("/logout", cookies={"lakehouse_session": cookie})

    assert response.status_code == 200
    assert get_session(cookie) is None


def test_query_requires_a_session():
    response = client.post("/query", json={"sql": "SELECT 1"})
    assert response.status_code == 401


def test_index_redirects_to_login_when_not_authenticated():
    response = client.get("/", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/login"


def test_index_serves_the_app_when_authenticated(monkeypatch):
    monkeypatch.setenv("POLARIS_ENDPOINT", "http://polaris:8181/api/catalog")
    monkeypatch.setattr(main_module, "polaris_login", lambda endpoint, cid, secret: "loader")
    login_response = client.post("/login", json={"client_id": "cid", "client_secret": "secret"})
    cookie = login_response.cookies.get("lakehouse_session")

    response = client.get("/", cookies={"lakehouse_session": cookie}, follow_redirects=False)

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_login_page_route_serves_html():
    response = client.get("/login")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
