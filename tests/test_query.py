import app.main as main_module
from app.catalog import CatalogConfigError
from fastapi.testclient import TestClient

from app.main import app
from app.session import create_session

client = TestClient(app)


class FakeResult:
    def __init__(self, columns, rows):
        self.description = [(c,) for c in columns] if columns else None
        self._rows = rows

    def fetchall(self):
        return self._rows


class FakeConnection:
    def __init__(self, columns, rows):
        self._columns = columns
        self._rows = rows
        self.executed = []

    def execute(self, sql):
        self.executed.append(sql)
        return FakeResult(self._columns, self._rows)


def _logged_in_cookie():
    session_id = create_session("cid", "secret", "loader")
    return {"lakehouse_session": session_id}


def test_query_runs_select_and_returns_rows(monkeypatch):
    fake = FakeConnection(["id", "name"], [[1, "a"], [2, "b"]])
    monkeypatch.setattr(
        main_module, "build_connection", lambda client_id, client_secret: fake
    )
    monkeypatch.setattr(main_module, "record_query", lambda **kwargs: None)

    response = client.post(
        "/query", json={"sql": "SELECT * FROM nyc_taxi.trips"}, cookies=_logged_in_cookie()
    )

    assert response.status_code == 200
    body = response.json()
    assert body == {"columns": ["id", "name"], "rows": [[1, "a"], [2, "b"]]}
    assert fake.executed == ["SELECT * FROM nyc_taxi.trips"]


def test_query_passes_the_session_principals_own_credentials(monkeypatch):
    captured = {}

    def fake_build_connection(client_id, client_secret):
        captured["client_id"] = client_id
        captured["client_secret"] = client_secret
        return FakeConnection(["x"], [[1]])

    monkeypatch.setattr(main_module, "build_connection", fake_build_connection)
    monkeypatch.setattr(main_module, "record_query", lambda **kwargs: None)

    client.post(
        "/query", json={"sql": "SELECT 1"}, cookies=_logged_in_cookie()
    )

    assert captured == {"client_id": "cid", "client_secret": "secret"}


def test_query_rejects_empty_sql():
    response = client.post("/query", json={"sql": "   "}, cookies=_logged_in_cookie())
    assert response.status_code == 400


def test_query_allows_write_statements_now(monkeypatch):
    # The app-level guard is gone — Polaris itself is the authorization
    # boundary now. A DDL statement should reach build_connection/execute,
    # not be rejected by the app.
    fake = FakeConnection([], [])
    monkeypatch.setattr(
        main_module, "build_connection", lambda client_id, client_secret: fake
    )
    monkeypatch.setattr(main_module, "record_query", lambda **kwargs: None)

    response = client.post(
        "/query",
        json={"sql": "CREATE TABLE lakehouse.nyc_taxi.x AS SELECT 1"},
        cookies=_logged_in_cookie(),
    )

    assert response.status_code == 200
    assert fake.executed == ["CREATE TABLE lakehouse.nyc_taxi.x AS SELECT 1"]


def test_query_allows_multi_statement_sql_now(monkeypatch):
    fake = FakeConnection(["x"], [[1]])
    monkeypatch.setattr(
        main_module, "build_connection", lambda client_id, client_secret: fake
    )
    monkeypatch.setattr(main_module, "record_query", lambda **kwargs: None)

    response = client.post(
        "/query",
        json={"sql": "CREATE TABLE t AS SELECT 1; SELECT * FROM t"},
        cookies=_logged_in_cookie(),
    )

    assert response.status_code == 200


def test_query_returns_500_on_catalog_config_error(monkeypatch):
    def raise_config_error(client_id, client_secret):
        raise CatalogConfigError("missing required environment variable POLARIS_ENDPOINT")

    monkeypatch.setattr(main_module, "build_connection", raise_config_error)

    response = client.post("/query", json={"sql": "SELECT 1"}, cookies=_logged_in_cookie())

    assert response.status_code == 500
    assert "POLARIS_ENDPOINT" in response.json()["detail"]


def test_query_returns_400_on_duckdb_error(monkeypatch):
    class RaisingConnection:
        def execute(self, sql):
            raise ValueError("Catalog Error: Table with name trips does not exist!")

    monkeypatch.setattr(
        main_module, "build_connection", lambda client_id, client_secret: RaisingConnection()
    )
    monkeypatch.setattr(main_module, "record_query", lambda **kwargs: None)

    response = client.post(
        "/query", json={"sql": "SELECT * FROM nyc_taxi.trips"}, cookies=_logged_in_cookie()
    )

    assert response.status_code == 400
    assert "does not exist" in response.json()["detail"]


def test_query_returns_401_without_a_session():
    response = client.post("/query", json={"sql": "SELECT 1"})
    assert response.status_code == 401
