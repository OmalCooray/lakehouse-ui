import app.main as main_module
from app.catalog import CatalogConfigError
from fastapi.testclient import TestClient

from app.main import app

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


def test_query_runs_select_and_returns_rows(monkeypatch):
    fake = FakeConnection(["id", "name"], [[1, "a"], [2, "b"]])
    monkeypatch.setattr(main_module, "build_connection", lambda: fake)

    response = client.post("/query", json={"sql": "SELECT * FROM nyc_taxi.trips"})

    assert response.status_code == 200
    body = response.json()
    assert body == {"columns": ["id", "name"], "rows": [[1, "a"], [2, "b"]]}
    assert fake.executed == ["SELECT * FROM nyc_taxi.trips"]


def test_query_rejects_empty_sql():
    response = client.post("/query", json={"sql": "   "})
    assert response.status_code == 400


def test_query_rejects_write_statements(monkeypatch):
    def fail_if_called():
        raise AssertionError("should not connect for a rejected statement")

    monkeypatch.setattr(main_module, "build_connection", fail_if_called)

    response = client.post("/query", json={"sql": "DROP TABLE nyc_taxi.trips"})

    assert response.status_code == 400
    assert "read-only" in response.json()["detail"]


def test_query_accepts_with_and_show_statements(monkeypatch):
    fake = FakeConnection(["x"], [[1]])
    monkeypatch.setattr(main_module, "build_connection", lambda: fake)

    for sql in ["WITH t AS (SELECT 1 AS x) SELECT * FROM t", "SHOW TABLES"]:
        response = client.post("/query", json={"sql": sql})
        assert response.status_code == 200, sql


def test_query_returns_500_on_catalog_config_error(monkeypatch):
    def raise_config_error():
        raise CatalogConfigError("missing required environment variable POLARIS_ENDPOINT")

    monkeypatch.setattr(main_module, "build_connection", raise_config_error)

    response = client.post("/query", json={"sql": "SELECT 1"})

    assert response.status_code == 500
    assert "POLARIS_ENDPOINT" in response.json()["detail"]


def test_query_returns_400_on_duckdb_error(monkeypatch):
    class RaisingConnection:
        def execute(self, sql):
            raise ValueError("Catalog Error: Table with name trips does not exist!")

    monkeypatch.setattr(main_module, "build_connection", lambda: RaisingConnection())

    response = client.post("/query", json={"sql": "SELECT * FROM nyc_taxi.trips"})

    assert response.status_code == 400
    assert "does not exist" in response.json()["detail"]


def test_query_rejects_multi_statement_sql(monkeypatch):
    def fail_if_called():
        raise AssertionError("should not connect for a rejected statement")

    monkeypatch.setattr(main_module, "build_connection", fail_if_called)

    response = client.post(
        "/query", json={"sql": "SELECT 1; DROP TABLE nyc_taxi.trips;"}
    )

    assert response.status_code == 400
    assert "single statement" in response.json()["detail"]


def test_query_accepts_single_trailing_semicolon(monkeypatch):
    fake = FakeConnection(["x"], [[1]])
    monkeypatch.setattr(main_module, "build_connection", lambda: fake)

    response = client.post("/query", json={"sql": "SELECT 1;"})

    assert response.status_code == 200
    assert response.json() == {"columns": ["x"], "rows": [[1]]}


def test_query_returns_500_on_generic_build_connection_error(monkeypatch):
    def raise_runtime_error():
        raise RuntimeError("Polaris unreachable: connection timed out")

    monkeypatch.setattr(main_module, "build_connection", raise_runtime_error)

    response = client.post("/query", json={"sql": "SELECT 1"})

    assert response.status_code == 500
    detail = response.json()["detail"]
    assert detail
    assert "Polaris unreachable" in detail
