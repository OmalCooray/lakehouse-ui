"""Build a DuckDB connection attached to the Polaris Iceberg REST catalog.

Configuration is entirely via environment variables so the same code runs
locally (port-forwarded) and in-cluster (values mounted from a Secret):

  POLARIS_ENDPOINT       e.g. http://polaris.lakehouse.svc.cluster.local:8181/api/catalog
  POLARIS_CLIENT_ID      the scoped principal's client id
  POLARIS_CLIENT_SECRET  the scoped principal's client secret
  POLARIS_CATALOG        e.g. lakehouse
"""
from __future__ import annotations

import os
from typing import Any, Protocol


class CatalogConfigError(RuntimeError):
    """Raised when a required POLARIS_* environment variable is missing."""


class ExecutableConnection(Protocol):
    def execute(self, sql: str) -> Any: ...


_REQUIRED_ENV_VARS = (
    "POLARIS_ENDPOINT",
    "POLARIS_CLIENT_ID",
    "POLARIS_CLIENT_SECRET",
    "POLARIS_CATALOG",
)


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise CatalogConfigError(f"missing required environment variable {name}")
    return value


def _sql_quote(value: str) -> str:
    """Escape a value for embedding as a single-quoted SQL string literal."""
    return value.replace("'", "''")


def _sql_identifier(value: str) -> str:
    """Escape a value for embedding as a double-quoted SQL identifier."""
    return '"' + value.replace('"', '""') + '"'


def build_connection(conn: ExecutableConnection | None = None) -> ExecutableConnection:
    """Return a DuckDB connection with the Polaris catalog attached.

    Pass an existing `conn` to attach onto it instead of opening a fresh
    in-memory DuckDB connection — this is what makes the attach logic
    testable with a stub in place of real DuckDB/network calls.
    """
    env = {name: _require_env(name) for name in _REQUIRED_ENV_VARS}
    endpoint = env["POLARIS_ENDPOINT"]
    client_id = env["POLARIS_CLIENT_ID"]
    client_secret = env["POLARIS_CLIENT_SECRET"]
    catalog = env["POLARIS_CATALOG"]

    if conn is None:
        import duckdb

        conn = duckdb.connect(":memory:")

    conn.execute("INSTALL iceberg")
    conn.execute("LOAD iceberg")
    conn.execute("INSTALL httpfs")
    conn.execute("LOAD httpfs")
    conn.execute(
        "CREATE OR REPLACE SECRET polaris_secret ("
        "TYPE iceberg, "
        f"CLIENT_ID '{_sql_quote(client_id)}', "
        f"CLIENT_SECRET '{_sql_quote(client_secret)}', "
        f"ENDPOINT '{_sql_quote(endpoint)}'"
        ")"
    )
    conn.execute(
        f"ATTACH '{_sql_quote(catalog)}' AS {_sql_identifier(catalog)} ("
        "TYPE iceberg, "
        f"ENDPOINT '{_sql_quote(endpoint)}', "
        "ACCESS_DELEGATION_MODE 'vended_credentials'"
        ")"
    )
    return conn
