"""lakehouse-ui: a minimal query UI for the MinIO/Iceberg/Polaris/DuckDB stack."""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app.catalog import CatalogConfigError, build_connection

app = FastAPI(title="lakehouse-ui")

_READ_ONLY_PREFIXES = ("select", "with", "show", "describe", "explain", "pragma", "call")


class QueryRequest(BaseModel):
    sql: str


class QueryResponse(BaseModel):
    columns: list[str]
    rows: list[list]


def _is_read_only(sql: str) -> bool:
    stripped = sql.strip()
    if not stripped:
        return False
    first_word = stripped.split(None, 1)[0].lower()
    return first_word in _READ_ONLY_PREFIXES


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.post("/query", response_model=QueryResponse)
def run_query(request: QueryRequest) -> QueryResponse:
    if not request.sql.strip():
        raise HTTPException(status_code=400, detail="sql must not be empty")
    if not _is_read_only(request.sql):
        raise HTTPException(
            status_code=400,
            detail="only read-only statements are allowed "
            f"({', '.join(_READ_ONLY_PREFIXES)})",
        )

    try:
        connection = build_connection()
    except CatalogConfigError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    try:
        result = connection.execute(request.sql)
        columns = [d[0] for d in result.description] if result.description else []
        rows = [list(row) for row in result.fetchall()]
    except Exception as exc:  # DuckDB/catalog errors surface as plain Exceptions
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return QueryResponse(columns=columns, rows=rows)
