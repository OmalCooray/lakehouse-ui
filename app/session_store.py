"""Session store for logged-in Polaris principals, backed by Postgres —
a `sessions` table in the same `lakehouse_ui` database query_history
already uses (see charts/polaris-postgres, and app.db for the shared
connection helper).

Replaces the old in-memory dict (app/session.py, now removed): a pod
restart no longer logs everyone out, and more than one replica can now
share sessions — confirmed live as the actual cause of a real incident
during the TPC-H load test, where an OOM-triggered restart wiped every
in-flight session.

Sessions expire after SESSION_TTL_HOURS, checked lazily at lookup time —
no separate cleanup job.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.db import ExecutableConnection, connect as _connect

SESSION_TTL_HOURS = 12


@dataclass
class Session:
    client_id: str
    client_secret: str = field(repr=False)
    principal_name: str


def ensure_schema(conn: ExecutableConnection | None = None) -> None:
    """Create the sessions table if it doesn't already exist. Safe to call
    on every app startup, same owns-connection contract as
    app.history.ensure_schema."""
    owns_conn = conn is None
    if conn is None:
        conn = _connect()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_id     TEXT PRIMARY KEY,
                client_id      TEXT NOT NULL,
                client_secret  TEXT NOT NULL,
                principal_name TEXT NOT NULL,
                created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        if owns_conn:
            conn.commit()
    finally:
        if owns_conn:
            conn.close()


def create_session(
    client_id: str,
    client_secret: str,
    principal_name: str,
    conn: ExecutableConnection | None = None,
) -> str:
    """Create a session, returning its ID (the cookie value)."""
    owns_conn = conn is None
    if conn is None:
        conn = _connect()
    try:
        session_id = secrets.token_urlsafe(32)
        conn.execute(
            """
            INSERT INTO sessions (session_id, client_id, client_secret, principal_name, created_at)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (session_id, client_id, client_secret, principal_name, datetime.now(timezone.utc)),
        )
        if owns_conn:
            conn.commit()
    finally:
        if owns_conn:
            conn.close()
    return session_id


def get_session(session_id: str | None, conn: ExecutableConnection | None = None) -> Session | None:
    if session_id is None:
        return None
    owns_conn = conn is None
    if conn is None:
        conn = _connect()
    try:
        cursor = conn.execute(
            "SELECT client_id, client_secret, principal_name, created_at FROM sessions WHERE session_id = %s",
            (session_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        client_id, client_secret, principal_name, created_at = row
        if datetime.now(timezone.utc) - created_at > timedelta(hours=SESSION_TTL_HOURS):
            conn.execute("DELETE FROM sessions WHERE session_id = %s", (session_id,))
            if owns_conn:
                conn.commit()
            return None
        return Session(client_id=client_id, client_secret=client_secret, principal_name=principal_name)
    finally:
        if owns_conn:
            conn.close()


def delete_session(session_id: str | None, conn: ExecutableConnection | None = None) -> None:
    if session_id is None:
        return
    owns_conn = conn is None
    if conn is None:
        conn = _connect()
    try:
        conn.execute("DELETE FROM sessions WHERE session_id = %s", (session_id,))
        if owns_conn:
            conn.commit()
    finally:
        if owns_conn:
            conn.close()
