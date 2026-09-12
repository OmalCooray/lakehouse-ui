"""lakehouse-ui: a minimal query UI for the MinIO/Iceberg/Polaris/DuckDB stack."""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import Cookie, Depends, FastAPI, HTTPException, Response
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.catalog import CatalogConfigError, build_connection
from app.polaris_auth import LoginError
from app.polaris_auth import login as polaris_login
from app.session import Session, create_session, delete_session, get_session

app = FastAPI(title="lakehouse-ui")

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

_READ_ONLY_PREFIXES = ("select", "with", "show", "describe", "explain", "pragma")


class QueryRequest(BaseModel):
    sql: str


class QueryResponse(BaseModel):
    columns: list[str]
    rows: list[list]


class LoginRequest(BaseModel):
    client_id: str
    client_secret: str


class LoginResponse(BaseModel):
    principal: str


def _is_read_only(sql: str) -> bool:
    stripped = sql.strip()
    if not stripped:
        return False
    first_word = stripped.split(None, 1)[0].lower()
    return first_word in _READ_ONLY_PREFIXES


def _is_single_statement(sql: str) -> bool:
    # Heuristic: strip at most one trailing semicolon, then reject if any
    # semicolon remains. This will also reject a query containing a literal
    # ";" inside a quoted string literal, but a false positive is the safe
    # direction for a security guard here.
    body = sql.strip()
    if body.endswith(";"):
        body = body[:-1]
    return ";" not in body


def require_session(
    lakehouse_session: str | None = Cookie(default=None),
) -> Session:
    session = get_session(lakehouse_session)
    if session is None:
        raise HTTPException(status_code=401, detail="not logged in")
    return session


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.post("/login", response_model=LoginResponse)
def login_route(request: LoginRequest, response: Response) -> LoginResponse:
    polaris_endpoint = os.environ.get("POLARIS_ENDPOINT")
    if not polaris_endpoint:
        raise HTTPException(
            status_code=500, detail="server missing POLARIS_ENDPOINT configuration"
        )
    try:
        principal_name = polaris_login(
            polaris_endpoint, request.client_id, request.client_secret
        )
    except LoginError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    session_id = create_session(request.client_id, request.client_secret, principal_name)
    response.set_cookie(
        key="lakehouse_session",
        value=session_id,
        httponly=True,
        samesite="lax",
    )
    return LoginResponse(principal=principal_name)


@app.post("/logout")
def logout_route(
    response: Response, lakehouse_session: str | None = Cookie(default=None)
) -> dict:
    delete_session(lakehouse_session)
    response.delete_cookie("lakehouse_session")
    return {"status": "ok"}


@app.get("/")
def index(lakehouse_session: str | None = Cookie(default=None)):
    if get_session(lakehouse_session) is None:
        return RedirectResponse(url="/login")
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/login")
def login_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "login.html")


@app.post("/query", response_model=QueryResponse)
def run_query(
    request: QueryRequest, session: Session = Depends(require_session)
) -> QueryResponse:
    if not request.sql.strip():
        raise HTTPException(status_code=400, detail="sql must not be empty")
    if not _is_read_only(request.sql):
        raise HTTPException(
            status_code=400,
            detail="only read-only statements are allowed "
            f"({', '.join(_READ_ONLY_PREFIXES)})",
        )
    if not _is_single_statement(request.sql):
        raise HTTPException(
            status_code=400,
            detail="only a single statement is allowed",
        )

    try:
        connection = build_connection()
    except CatalogConfigError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"failed to connect to catalog: {exc}"
        ) from exc

    try:
        result = connection.execute(request.sql)
        columns = [d[0] for d in result.description] if result.description else []
        rows = [list(row) for row in result.fetchall()]
    except Exception as exc:  # DuckDB/catalog errors surface as plain Exceptions
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return QueryResponse(columns=columns, rows=rows)
