from datetime import datetime, timedelta, timezone

from app.session_store import Session, create_session, delete_session, ensure_schema, get_session


class FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeConnection:
    """In-memory stand-in for a psycopg connection, shaped around exactly
    the SQL app.session_store issues — mirrors tests/test_history.py's
    own FakeConnection pattern for this codebase's Postgres-backed
    modules."""

    def __init__(self):
        self.executed = []
        self.committed = False
        self.closed = False
        self._table: dict[str, tuple] = {}

    def execute(self, sql, params=()):
        self.executed.append((sql, params))
        normalized = " ".join(sql.split()).upper()
        if normalized.startswith("CREATE TABLE"):
            return self
        if normalized.startswith("INSERT INTO SESSIONS"):
            session_id, client_id, client_secret, principal_name, created_at = params
            self._table[session_id] = (client_id, client_secret, principal_name, created_at)
            return self
        if normalized.startswith("SELECT"):
            session_id = params[0]
            row = self._table.get(session_id)
            return FakeCursor([row] if row else [])
        if normalized.startswith("DELETE"):
            session_id = params[0]
            self._table.pop(session_id, None)
            return self
        raise AssertionError(f"unexpected SQL: {sql}")

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True


def test_create_session_returns_an_id_and_stores_it(monkeypatch):
    fake = FakeConnection()

    session_id = create_session("cid", "secret", "loader", conn=fake)

    assert isinstance(session_id, str) and len(session_id) > 20
    assert session_id in fake._table


def test_get_session_returns_the_stored_session(monkeypatch):
    fake = FakeConnection()
    session_id = create_session("cid", "secret", "loader", conn=fake)

    session = get_session(session_id, conn=fake)

    assert session == Session(client_id="cid", client_secret="secret", principal_name="loader")


def test_get_session_returns_none_for_unknown_id(monkeypatch):
    fake = FakeConnection()

    assert get_session("not-a-real-id", conn=fake) is None


def test_get_session_returns_none_for_none_id(monkeypatch):
    fake = FakeConnection()

    assert get_session(None, conn=fake) is None


def test_get_session_returns_none_and_deletes_an_expired_session(monkeypatch):
    fake = FakeConnection()
    session_id = create_session("cid", "secret", "loader", conn=fake)
    # Backdate it past the 12-hour TTL.
    client_id, client_secret, principal_name, _created_at = fake._table[session_id]
    fake._table[session_id] = (
        client_id,
        client_secret,
        principal_name,
        datetime.now(timezone.utc) - timedelta(hours=13),
    )

    session = get_session(session_id, conn=fake)

    assert session is None
    assert session_id not in fake._table


def test_delete_session_removes_it(monkeypatch):
    fake = FakeConnection()
    session_id = create_session("cid", "secret", "loader", conn=fake)

    delete_session(session_id, conn=fake)

    assert session_id not in fake._table
    assert get_session(session_id, conn=fake) is None


def test_delete_session_is_a_no_op_for_none_id(monkeypatch):
    fake = FakeConnection()

    delete_session(None, conn=fake)  # must not raise


def test_create_session_owns_and_closes_its_own_connection_when_none_given(monkeypatch):
    fake = FakeConnection()
    monkeypatch.setattr("app.session_store._connect", lambda: fake)

    create_session("cid", "secret", "loader")

    assert fake.committed is True
    assert fake.closed is True


def test_get_session_owns_and_closes_its_own_connection_when_none_given(monkeypatch):
    fake = FakeConnection()
    monkeypatch.setattr("app.session_store._connect", lambda: fake)
    session_id = create_session("cid", "secret", "loader", conn=fake)
    fake.closed = False  # reset after the setup call above

    get_session(session_id)

    assert fake.closed is True


def test_ensure_schema_creates_the_table(monkeypatch):
    fake = FakeConnection()

    ensure_schema(conn=fake)

    assert any("CREATE TABLE" in sql.upper() for sql, _ in fake.executed)
