"""Shared Postgres connection helper for lakehouse_ui's own tables
(query_history, sessions) — a dedicated database on the existing
polaris-postgres instance (see charts/polaris-postgres).

Connection info comes from environment variables (LAKEHOUSE_UI_DB_HOST/
_NAME/_USER/_PASSWORD), the same explicit-env-var pattern app.catalog
uses for POLARIS_* — no ORM.
"""
from __future__ import annotations

import os
from typing import Any, Protocol


class DBConfigError(RuntimeError):
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
        raise DBConfigError(f"missing required environment variable {name}")
    return value


def connect() -> ExecutableConnection:
    env = {name: _require_env(name) for name in _REQUIRED_ENV_VARS}
    import psycopg

    return psycopg.connect(
        host=env["LAKEHOUSE_UI_DB_HOST"],
        dbname=env["LAKEHOUSE_UI_DB_NAME"],
        user=env["LAKEHOUSE_UI_DB_USER"],
        password=env["LAKEHOUSE_UI_DB_PASSWORD"],
        connect_timeout=5,
    )
