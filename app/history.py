"""Query history, stored in Postgres — a dedicated `lakehouse_ui` database
on the existing polaris-postgres instance (see charts/polaris-postgres).

Connection handling lives in app.db (shared with app.session_store).
"""
from __future__ import annotations

from app.db import DBConfigError, ExecutableConnection, connect as _connect

# Old name, kept as an alias — nothing importing HistoryConfigError from
# here should need to change.
HistoryConfigError = DBConfigError


def ensure_schema(conn: ExecutableConnection | None = None) -> None:
    """Create the query_history table/index if they don't already exist.
    Safe to call on every app startup. If `conn` is not given, this opens
    its own connection and closes it; if `conn` IS given (tests, or a
    caller managing its own transaction), this never commits/closes it —
    that's the caller's responsibility.
    """
    owns_conn = conn is None
    if conn is None:
        conn = _connect()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS query_history (
                id            BIGSERIAL PRIMARY KEY,
                principal     TEXT NOT NULL,
                sql_text      TEXT NOT NULL,
                status        TEXT NOT NULL,
                row_count     INTEGER,
                error_message TEXT,
                duration_ms   INTEGER NOT NULL,
                run_at        TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS query_history_principal_run_at "
            "ON query_history (principal, run_at DESC)"
        )
        if owns_conn:
            conn.commit()
    finally:
        if owns_conn:
            conn.close()


def record_query(
    principal: str,
    sql_text: str,
    status: str,
    duration_ms: int,
    row_count: int | None = None,
    error_message: str | None = None,
    conn: ExecutableConnection | None = None,
) -> None:
    """Insert one query_history row. If `conn` is not given, opens/commits/
    closes its own connection; if `conn` IS given, never commits/closes it —
    that's the caller's responsibility.
    """
    owns_conn = conn is None
    if conn is None:
        conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO query_history
                (principal, sql_text, status, row_count, error_message, duration_ms)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (principal, sql_text, status, row_count, error_message, duration_ms),
        )
        if owns_conn:
            conn.commit()
    finally:
        if owns_conn:
            conn.close()


def get_history(
    principal: str, limit: int = 50, offset: int = 0, conn: ExecutableConnection | None = None
) -> list[dict]:
    owns_conn = conn is None
    if conn is None:
        conn = _connect()
    try:
        cursor = conn.execute(
            """
            SELECT id, sql_text, status, row_count, error_message, duration_ms, run_at
            FROM query_history
            WHERE principal = %s
            ORDER BY run_at DESC
            LIMIT %s OFFSET %s
            """,
            (principal, limit, offset),
        )
        rows = cursor.fetchall()
        # NOTE: row[0]..row[6] below is positional and must stay in sync with
        # this SELECT's column order.
        result = [
            {
                "id": row[0],
                "sql_text": row[1],
                "status": row[2],
                "row_count": row[3],
                "error_message": row[4],
                "duration_ms": row[5],
                "run_at": row[6].isoformat(),
            }
            for row in rows
        ]
    finally:
        if owns_conn:
            conn.close()
    return result
