"""In-memory session store for logged-in Polaris principals.

Sessions are keyed by a random session ID (the value of the session
cookie) and hold the principal's own Polaris credentials plus its name.
Deliberately in-memory (not Postgres/Redis) — acceptable at `replicas: 1`
(this chart's existing setup); a pod restart logs everyone out, a fine
trade for the simplicity.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class Session:
    client_id: str
    client_secret: str = field(repr=False)
    principal_name: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


_sessions: dict[str, Session] = {}


def create_session(client_id: str, client_secret: str, principal_name: str) -> str:
    """Create a session, returning its ID (the cookie value)."""
    session_id = secrets.token_urlsafe(32)
    _sessions[session_id] = Session(
        client_id=client_id,
        client_secret=client_secret,
        principal_name=principal_name,
    )
    return session_id


def get_session(session_id: str | None) -> Session | None:
    if session_id is None:
        return None
    return _sessions.get(session_id)


def delete_session(session_id: str | None) -> None:
    if session_id is not None:
        _sessions.pop(session_id, None)
