import app.main as main_module
from fastapi.testclient import TestClient

from app.main import app
from app.session import create_session

client = TestClient(app)


def _logged_in_cookie(principal="loader"):
    session_id = create_session("cid", "secret", principal)
    return {"lakehouse_session": session_id}


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

    def execute(self, sql):
        return FakeResult(self._columns, self._rows)


def test_history_requires_a_session():
    response = client.get("/history")
    assert response.status_code == 401


def test_history_returns_the_current_principals_rows(monkeypatch):
    captured = {}

    def fake_get_history(principal, limit=50, offset=0):
        captured["principal"] = principal
        captured["limit"] = limit
        captured["offset"] = offset
        return [
            {
                "id": 1,
                "sql_text": "SELECT 1",
                "status": "success",
                "row_count": 1,
                "error_message": None,
                "duration_ms": 5,
                "run_at": "2026-09-13T12:00:00+00:00",
            }
        ]

    monkeypatch.setattr(main_module, "get_history", fake_get_history)

    response = client.get("/history", cookies=_logged_in_cookie("loader"))

    assert response.status_code == 200
    assert response.json()["history"][0]["sql_text"] == "SELECT 1"
    assert captured["principal"] == "loader"
    assert captured["limit"] == 50
    assert captured["offset"] == 0


def test_history_accepts_limit_and_offset_query_params(monkeypatch):
    captured = {}

    def fake_get_history(principal, limit=50, offset=0):
        captured["limit"] = limit
        captured["offset"] = offset
        return []

    monkeypatch.setattr(main_module, "get_history", fake_get_history)

    client.get("/history?limit=10&offset=20", cookies=_logged_in_cookie())

    assert captured == {"limit": 10, "offset": 20}


def test_successful_query_is_recorded_in_history(monkeypatch):
    fake_conn = FakeConnection(["x"], [[1]])
    monkeypatch.setattr(
        main_module, "build_connection", lambda client_id, client_secret: fake_conn
    )
    recorded = []
    monkeypatch.setattr(
        main_module,
        "record_query",
        lambda **kwargs: recorded.append(kwargs),
    )

    client.post("/query", json={"sql": "SELECT 1"}, cookies=_logged_in_cookie("loader"))

    assert len(recorded) == 1
    assert recorded[0]["principal"] == "loader"
    assert recorded[0]["sql_text"] == "SELECT 1"
    assert recorded[0]["status"] == "success"
    assert recorded[0]["row_count"] == 1
    assert recorded[0]["error_message"] is None
    assert isinstance(recorded[0]["duration_ms"], int)


def test_failed_query_is_recorded_in_history_too(monkeypatch):
    class RaisingConnection:
        def execute(self, sql):
            raise ValueError("boom")

    monkeypatch.setattr(
        main_module, "build_connection", lambda client_id, client_secret: RaisingConnection()
    )
    recorded = []
    monkeypatch.setattr(
        main_module,
        "record_query",
        lambda **kwargs: recorded.append(kwargs),
    )

    client.post("/query", json={"sql": "SELECT bad"}, cookies=_logged_in_cookie("loader"))

    assert len(recorded) == 1
    assert recorded[0]["status"] == "error"
    assert recorded[0]["row_count"] is None
    assert "boom" in recorded[0]["error_message"]
