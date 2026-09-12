import datetime

import pytest

from app.history import HistoryConfigError, ensure_schema, get_history, record_query


class FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class FakeConnection:
    def __init__(self, fetch_rows=None):
        self.executed = []
        self._fetch_rows = fetch_rows or []
        self.committed = False
        self.closed = False

    def execute(self, sql, params=()):
        self.executed.append((sql, params))
        return FakeCursor(self._fetch_rows)

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True


REQUIRED_ENV = {
    "LAKEHOUSE_UI_DB_HOST": "polaris-postgres",
    "LAKEHOUSE_UI_DB_NAME": "lakehouse_ui",
    "LAKEHOUSE_UI_DB_USER": "lakehouse_ui",
    "LAKEHOUSE_UI_DB_PASSWORD": "s3cr3t",
}


def _set_env(monkeypatch, **overrides):
    values = {**REQUIRED_ENV, **overrides}
    for key, value in values.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)


def test_ensure_schema_creates_table_and_index_and_does_not_own_the_connection():
    fake = FakeConnection()

    ensure_schema(conn=fake)

    joined = "\n".join(sql for sql, _ in fake.executed)
    assert "CREATE TABLE IF NOT EXISTS query_history" in joined
    assert "CREATE INDEX IF NOT EXISTS query_history_principal_run_at" in joined
    assert fake.committed is False
    assert fake.closed is False


def test_record_query_inserts_a_success_row():
    fake = FakeConnection()

    record_query(
        principal="loader",
        sql_text="SELECT 1",
        status="success",
        duration_ms=42,
        row_count=1,
        conn=fake,
    )

    sql, params = fake.executed[0]
    assert "INSERT INTO query_history" in sql
    assert params == ("loader", "SELECT 1", "success", 1, None, 42)


def test_record_query_inserts_an_error_row():
    fake = FakeConnection()

    record_query(
        principal="lakehouse-ui",
        sql_text="DROP TABLE x",
        status="error",
        duration_ms=5,
        error_message="not authorized",
        conn=fake,
    )

    _, params = fake.executed[0]
    assert params == ("lakehouse-ui", "DROP TABLE x", "error", None, "not authorized", 5)


def test_get_history_returns_rows_for_the_given_principal():
    run_at = datetime.datetime(2026, 9, 13, 12, 0, 0, tzinfo=datetime.timezone.utc)
    fake = FakeConnection(
        fetch_rows=[(1, "SELECT 1", "success", 1, None, 10, run_at)]
    )

    result = get_history("loader", conn=fake)

    assert result == [
        {
            "id": 1,
            "sql_text": "SELECT 1",
            "status": "success",
            "row_count": 1,
            "error_message": None,
            "duration_ms": 10,
            "run_at": "2026-09-13T12:00:00+00:00",
        }
    ]
    sql, params = fake.executed[0]
    assert "WHERE principal = %s" in sql
    assert params == ("loader", 50, 0)


def test_get_history_respects_limit_and_offset():
    fake = FakeConnection(fetch_rows=[])

    get_history("loader", limit=10, offset=20, conn=fake)

    _, params = fake.executed[0]
    assert params == ("loader", 10, 20)


def test_get_history_requires_env_vars_when_no_conn_given(monkeypatch):
    _set_env(monkeypatch, LAKEHOUSE_UI_DB_HOST=None)
    with pytest.raises(HistoryConfigError, match="LAKEHOUSE_UI_DB_HOST"):
        get_history("loader")
