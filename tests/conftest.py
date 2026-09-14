"""Shared pytest fixtures.

fake_session_store replaces app.main's imported create_session/
get_session/delete_session with a simple in-memory dict for every test
in this suite (autouse — no test needs to opt in). app.session_store's
own Postgres-backed logic is tested directly and thoroughly in
tests/test_session_store.py; nothing outside that file should need a
real Postgres connection just to get a valid session for an unrelated
route test.
"""
import pytest

import app.main as main_module
from app.session_store import Session


@pytest.fixture(autouse=True)
def fake_session_store(monkeypatch):
    store: dict[str, Session] = {}

    def fake_create_session(client_id, client_secret, principal_name):
        session_id = f"test-session-{len(store)}-{principal_name}"
        store[session_id] = Session(
            client_id=client_id, client_secret=client_secret, principal_name=principal_name
        )
        return session_id

    def fake_get_session(session_id):
        return store.get(session_id) if session_id else None

    def fake_delete_session(session_id):
        if session_id is not None:
            store.pop(session_id, None)

    monkeypatch.setattr(main_module, "create_session", fake_create_session)
    monkeypatch.setattr(main_module, "get_session", fake_get_session)
    monkeypatch.setattr(main_module, "delete_session", fake_delete_session)
