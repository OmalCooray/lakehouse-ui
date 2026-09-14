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

    def fetchmany(self, n):
        # Real DuckDB cursors consume from wherever the last fetch left off;
        # this fake only ever gets one fetchmany call per query in
        # app.main.run_query, so a plain slice is enough to exercise the
        # truncation logic without needing full cursor-position tracking.
        return self._rows[:n]


class FakeConnection:
    def __init__(self, columns, rows):
        self._columns = columns
        self._rows = rows
        self.executed = []

    def execute(self, sql):
        self.executed.append(sql)
        return FakeResult(self._columns, self._rows)


def _logged_in_cookie():
    session_id = main_module.create_session("cid", "secret", "loader")
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
    assert body["columns"] == ["id", "name"]
    assert body["rows"] == [[1, "a"], [2, "b"]]
    assert body["truncated"] is False
    assert fake.executed == ["SELECT * FROM nyc_taxi.trips"]


def test_query_response_includes_duration_and_row_count(monkeypatch):
    fake = FakeConnection(["id"], [[1], [2], [3]])
    monkeypatch.setattr(
        main_module, "build_connection", lambda client_id, client_secret: fake
    )
    monkeypatch.setattr(main_module, "record_query", lambda **kwargs: None)

    response = client.post(
        "/query", json={"sql": "SELECT * FROM t"}, cookies=_logged_in_cookie()
    )

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body["duration_ms"], int)
    assert body["duration_ms"] >= 0
    assert body["row_count"] == 3


def test_query_caps_rows_and_reports_truncation(monkeypatch):
    # Real bug, found live (2026-09-14): an unbounded `SELECT *` against a
    # multi-million-row table serialized the whole result set in-process,
    # starving the GIL long enough that /healthz missed its liveness
    # deadline and Kubernetes killed the pod mid-query. Fetching (and thus
    # serializing) at most MAX_RESULT_ROWS is what actually prevents that,
    # regardless of what the user's query asks for.
    monkeypatch.setattr(main_module, "MAX_RESULT_ROWS", 3)
    fake = FakeConnection(["id"], [[1], [2], [3], [4], [5]])
    monkeypatch.setattr(
        main_module, "build_connection", lambda client_id, client_secret: fake
    )
    monkeypatch.setattr(main_module, "record_query", lambda **kwargs: None)

    response = client.post(
        "/query", json={"sql": "SELECT * FROM huge_table"}, cookies=_logged_in_cookie()
    )

    assert response.status_code == 200
    body = response.json()
    assert body["rows"] == [[1], [2], [3]]
    assert body["truncated"] is True


def test_query_reports_no_truncation_when_row_count_is_under_the_cap(monkeypatch):
    monkeypatch.setattr(main_module, "MAX_RESULT_ROWS", 10)
    fake = FakeConnection(["id"], [[1], [2]])
    monkeypatch.setattr(
        main_module, "build_connection", lambda client_id, client_secret: fake
    )
    monkeypatch.setattr(main_module, "record_query", lambda **kwargs: None)

    response = client.post(
        "/query", json={"sql": "SELECT * FROM small_table"}, cookies=_logged_in_cookie()
    )

    assert response.status_code == 200
    body = response.json()
    assert body["rows"] == [[1], [2]]
    assert body["truncated"] is False


def test_query_returns_429_when_the_concurrency_limit_is_already_held(monkeypatch):
    monkeypatch.setattr(main_module, "_query_semaphore", __import__("threading").Semaphore(0))
    # A semaphore initialized to 0 has no permits to give — the very
    # first acquire attempt fails, exactly like every slot already being
    # held by other in-flight queries.

    response = client.post("/query", json={"sql": "SELECT 1"}, cookies=_logged_in_cookie())

    assert response.status_code == 429
    assert "concurrent" in response.json()["detail"].lower()


def test_query_releases_its_concurrency_slot_even_when_the_query_errors(monkeypatch):
    import threading

    class RaisingConnection:
        def execute(self, sql):
            raise ValueError("boom")

    monkeypatch.setattr(
        main_module, "build_connection", lambda client_id, client_secret: RaisingConnection()
    )
    monkeypatch.setattr(main_module, "record_query", lambda **kwargs: None)
    semaphore = threading.Semaphore(1)
    monkeypatch.setattr(main_module, "_query_semaphore", semaphore)

    response = client.post("/query", json={"sql": "SELECT 1"}, cookies=_logged_in_cookie())

    assert response.status_code == 400  # the query's own error, not a 429
    # If the slot wasn't released, this acquire would fail (already at 0).
    assert semaphore.acquire(blocking=False) is True


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


def test_query_still_succeeds_even_if_recording_history_fails(monkeypatch):
    fake = FakeConnection(["x"], [[1]])
    monkeypatch.setattr(main_module, "build_connection", lambda client_id, client_secret: fake)

    def raise_on_record(**kwargs):
        raise RuntimeError("history db unreachable")

    monkeypatch.setattr(main_module, "record_query", raise_on_record)

    response = client.post("/query", json={"sql": "SELECT 1"}, cookies=_logged_in_cookie())

    assert response.status_code == 200


def test_query_error_is_not_masked_if_recording_history_also_fails(monkeypatch):
    class RaisingConnection:
        def execute(self, sql):
            raise ValueError("Catalog Error: Table with name trips does not exist!")

    monkeypatch.setattr(
        main_module, "build_connection", lambda client_id, client_secret: RaisingConnection()
    )

    def raise_on_record(**kwargs):
        raise RuntimeError("history db unreachable")

    monkeypatch.setattr(main_module, "record_query", raise_on_record)

    response = client.post(
        "/query", json={"sql": "SELECT * FROM nyc_taxi.trips"}, cookies=_logged_in_cookie()
    )

    assert response.status_code == 400
    assert "does not exist" in response.json()["detail"]
