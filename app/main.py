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
from app.polaris_client import (
    PolarisClientError,
    get_principal_roles,
    get_table_schema,
    list_namespaces,
    list_tables,
)
from app.session import Session, create_session, delete_session, get_session

app = FastAPI(title="lakehouse-ui")

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


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


def require_session(
    lakehouse_session: str | None = Cookie(default=None),
) -> Session:
    session = get_session(lakehouse_session)
    if session is None:
        raise HTTPException(status_code=401, detail="not logged in")
    return session


def _catalog_config() -> tuple[str, str]:
    endpoint = os.environ.get("POLARIS_ENDPOINT")
    catalog = os.environ.get("POLARIS_CATALOG")
    if not endpoint or not catalog:
        raise HTTPException(
            status_code=500,
            detail="server missing POLARIS_ENDPOINT/POLARIS_CATALOG configuration",
        )
    return endpoint, catalog


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
    if not request.client_id or not request.client_secret:
        raise HTTPException(status_code=400, detail="client_id and client_secret are required")
    try:
        principal_name = polaris_login(
            polaris_endpoint, request.client_id, request.client_secret
        )
    except LoginError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    session_id = create_session(request.client_id, request.client_secret, principal_name)
    # secure=True omitted: this app is only ever reached via
    # kubectl port-forward over plain HTTP today (no TLS/Ingress anywhere
    # in this stack) — a Secure cookie would silently break login.
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


@app.get("/", response_model=None)
def index(lakehouse_session: str | None = Cookie(default=None)) -> FileResponse | RedirectResponse:
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

    try:
        connection = build_connection(session.client_id, session.client_secret)
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


@app.get("/catalog/namespaces")
def catalog_namespaces(session: Session = Depends(require_session)) -> dict:
    endpoint, catalog = _catalog_config()
    try:
        namespaces = list_namespaces(endpoint, session.client_id, session.client_secret, catalog)
    except PolarisClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"namespaces": namespaces}


@app.get("/catalog/tables/{namespace}")
def catalog_tables(
    namespace: str, session: Session = Depends(require_session)
) -> dict:
    endpoint, catalog = _catalog_config()
    try:
        tables = list_tables(
            endpoint, session.client_id, session.client_secret, catalog, namespace
        )
    except PolarisClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"tables": tables}


@app.get("/catalog/tables/{namespace}/{table}/schema")
def catalog_table_schema(
    namespace: str, table: str, session: Session = Depends(require_session)
) -> dict:
    endpoint, catalog = _catalog_config()
    try:
        fields = get_table_schema(
            endpoint, session.client_id, session.client_secret, catalog, namespace, table
        )
    except PolarisClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"fields": fields}


@app.get("/me")
def me_route(session: Session = Depends(require_session)) -> dict:
    root_client_id = os.environ.get("POLARIS_ROOT_CLIENT_ID")
    root_client_secret = os.environ.get("POLARIS_ROOT_CLIENT_SECRET")
    management_endpoint = os.environ.get("POLARIS_MANAGEMENT_ENDPOINT")
    if not root_client_id or not root_client_secret or not management_endpoint:
        raise HTTPException(
            status_code=500,
            detail="server missing POLARIS_ROOT_CLIENT_ID/POLARIS_ROOT_CLIENT_SECRET/"
            "POLARIS_MANAGEMENT_ENDPOINT configuration",
        )
    try:
        roles = get_principal_roles(
            management_endpoint, root_client_id, root_client_secret, session.principal_name
        )
    except PolarisClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"principal": session.principal_name, "roles": roles}
