"""Query history, stored in Postgres — a dedicated `lakehouse_ui` database
on the existing polaris-postgres instance (see charts/polaris-postgres).

Connection info comes from environment variables (LAKEHOUSE_UI_DB_HOST/
_NAME/_USER/_PASSWORD), the same explicit-env-var pattern app.catalog uses
for POLARIS_* — no ORM.
"""
from __future__ import annotations

import os
from typing import Any, Protocol


class HistoryConfigError(RuntimeError):
    """Raised when a required LAKEHOUSE_UI_DB_* environment variable is missing."""


class ExecutableConnection(Protocol):
    def execute(self, sql: str, params: tuple = ()) -> Any: ...
    def commit(self) -> None: ...
    def close(self) -> None: ...


_REQUIRED_ENV_VARS = (
    "LAKEHOUSE_UI_DB_HOST",
    "LAKEHOUSE_UI_DB_NAME",
    "LAKEHOUSE_UI_DB_USER",
    "LAKEHOUSE_UI_DB_PASSWORD",
)


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise HistoryConfigError(f"missing required environment variable {name}")
    return value


def _connect() -> ExecutableConnection:
    env = {name: _require_env(name) for name in _REQUIRED_ENV_VARS}
    import psycopg

    return psycopg.connect(
        host=env["LAKEHOUSE_UI_DB_HOST"],
        dbname=env["LAKEHOUSE_UI_DB_NAME"],
        user=env["LAKEHOUSE_UI_DB_USER"],
        password=env["LAKEHOUSE_UI_DB_PASSWORD"],
        connect_timeout=5,
    )


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
