"""lakehouse-ui: a minimal query UI for the MinIO/Iceberg/Polaris/DuckDB stack."""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from fastapi import Cookie, Depends, FastAPI, HTTPException, Query, Response
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.catalog import CatalogConfigError, build_connection
from app.history import ensure_schema, get_history, record_query
from app.polaris_auth import LoginError
from app.polaris_auth import login as polaris_login
from app.polaris_client import (
    PolarisClientError,
    get_catalog_roles_for_principal_role,
    get_grants_for_catalog_role,
    get_namespace_details,
    get_principal_roles,
    get_table_details,
    get_table_schema,
    list_namespaces,
    list_principals,
    list_tables,
)
from app.session_store import (
    Session,
    create_session,
    delete_session,
    ensure_schema as ensure_session_schema,
    get_session,
)

app = FastAPI(title="lakehouse-ui")


@app.on_event("startup")
def _init_history_schema() -> None:
    try:
        ensure_schema()
    except Exception as exc:  # noqa: BLE001 — don't crash the whole app if
        # Postgres isn't reachable yet at startup; /history and query
        # recording will just fail per-request until it is, same as any
        # other downstream-dependency-not-ready case this app already
        # tolerates (e.g. Polaris being briefly unreachable).
        print(f"WARNING: could not initialize query_history schema at startup: {exc}")

    try:
        ensure_session_schema()
    except Exception as exc:  # noqa: BLE001 — same reasoning as above;
        # login/query will fail per-request until Postgres is reachable,
        # rather than crash-looping the whole app at startup.
        print(f"WARNING: could not initialize sessions schema at startup: {exc}")


STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.middleware("http")
async def _no_cache_for_static(request, call_next):
    # Starlette's StaticFiles sends ETag/Last-Modified but no Cache-Control,
    # so browsers fall back to heuristic freshness and can keep serving a
    # pre-redeploy app.js/index.html for a long time with no revalidation —
    # confirmed live (2026-09-14): a fix landed in a new image, the pod
    # redeployed cleanly, and multiple fresh browser tabs still ran the old
    # JS. `no-cache` (not `no-store`) still lets the browser revalidate via
    # the existing ETag on a normal GET, so this costs a 304 round trip per
    # load, not a full re-download every time.
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


class QueryRequest(BaseModel):
    sql: str


# Hard cap on rows returned from any single query. Without this, an
# unbounded `SELECT *` against a multi-million-row table (confirmed live,
# 2026-09-14: `fct_trips`, ~2.9M rows) materializes the whole result set and
# JSON-serializes it in this same process — Python's GIL means that
# CPU-bound serialization loop starves every other thread, including the
# one answering /healthz, long enough that Kubernetes' liveness probe times
# out and kills the pod mid-query. Capping the fetch itself (not just
# truncating after the fact) keeps a huge/unbounded query cheap regardless
# of what the user types, matching a real query console's behavior
# (Snowflake/BigQuery cap interactive result previews too).
MAX_RESULT_ROWS = 10_000

# Caps concurrent DuckDB executions per pod. Confirmed live (2026-09-14):
# with no admission control at all, 5 concurrent large joins over
# lineitem-scale data blew well past the container's memory limit and
# OOMKilled the pod — this bounds it instead of just raising the limit
# and hoping. A request that can't get a slot immediately fails fast with
# 429 rather than queueing (a queue just delays the same OOM).
MAX_CONCURRENT_QUERIES = int(os.environ.get("MAX_CONCURRENT_QUERIES", "3"))
_query_semaphore = threading.Semaphore(MAX_CONCURRENT_QUERIES)


class QueryResponse(BaseModel):
    columns: list[str]
    rows: list[list]
    truncated: bool = False


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

    if not _query_semaphore.acquire(blocking=False):
        raise HTTPException(
            status_code=429, detail="Too many concurrent queries — try again in a moment"
        )
    try:
        return _run_query_body(request, session)
    finally:
        _query_semaphore.release()


def _run_query_body(request: QueryRequest, session: Session) -> QueryResponse:
    try:
        connection = build_connection(session.client_id, session.client_secret)
    except CatalogConfigError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"failed to connect to catalog: {exc}"
        ) from exc

    started_at = time.monotonic()
    try:
        result = connection.execute(request.sql)
        columns = [d[0] for d in result.description] if result.description else []
        # Fetch one row past the cap so we can tell "exactly MAX_RESULT_ROWS
        # rows" apart from "more rows exist" without a second query.
        fetched = result.fetchmany(MAX_RESULT_ROWS + 1)
        truncated = len(fetched) > MAX_RESULT_ROWS
        rows = [list(row) for row in fetched[:MAX_RESULT_ROWS]]
    except Exception as exc:  # DuckDB/catalog errors surface as plain Exceptions
        duration_ms = int((time.monotonic() - started_at) * 1000)
        try:
            record_query(
                principal=session.principal_name,
                sql_text=request.sql,
                status="error",
                duration_ms=duration_ms,
                row_count=None,
                error_message=str(exc),
            )
        except Exception as history_exc:  # noqa: BLE001 — history logging must
            # never affect the response the user gets, including hiding a real
            # query error behind a history-store failure.
            print(f"WARNING: failed to record query history: {history_exc}")
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    duration_ms = int((time.monotonic() - started_at) * 1000)
    try:
        record_query(
            principal=session.principal_name,
            sql_text=request.sql,
            status="success",
            duration_ms=duration_ms,
            row_count=len(rows),
            error_message=None,
        )
    except Exception as history_exc:  # noqa: BLE001 — history logging must never
        # affect the response the user gets.
        print(f"WARNING: failed to record query history: {history_exc}")
    return QueryResponse(columns=columns, rows=rows, truncated=truncated)


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


@app.get("/catalog/tables/{namespace}/{table}/details")
def catalog_table_details(
    namespace: str, table: str, session: Session = Depends(require_session)
) -> dict:
    endpoint, catalog = _catalog_config()
    try:
        details = get_table_details(
            endpoint, session.client_id, session.client_secret, catalog, namespace, table
        )
    except PolarisClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return details


@app.get("/catalog/namespaces/{namespace}/details")
def catalog_namespace_details(
    namespace: str, session: Session = Depends(require_session)
) -> dict:
    endpoint, catalog = _catalog_config()
    try:
        details = get_namespace_details(
            endpoint, session.client_id, session.client_secret, catalog, namespace
        )
        tables = list_tables(
            endpoint, session.client_id, session.client_secret, catalog, namespace
        )
    except PolarisClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    details["table_count"] = len(tables)
    return details


@app.get("/me")
def me_route(session: Session = Depends(require_session)) -> dict:
    root_client_id = os.environ.get("POLARIS_ROOT_CLIENT_ID")
    root_client_secret = os.environ.get("POLARIS_ROOT_CLIENT_SECRET")
    # get_principal_roles needs both: catalog_endpoint for the OAuth token
    # exchange (Polaris's /v1/oauth/tokens only exists under the Catalog
    # API base path, not the Management one — confirmed live), and
    # management_endpoint for the actual principal-roles lookup.
    catalog_endpoint = os.environ.get("POLARIS_ENDPOINT")
    management_endpoint = os.environ.get("POLARIS_MANAGEMENT_ENDPOINT")
    if not root_client_id or not root_client_secret or not catalog_endpoint or not management_endpoint:
        raise HTTPException(
            status_code=500,
            detail="server missing POLARIS_ROOT_CLIENT_ID/POLARIS_ROOT_CLIENT_SECRET/"
            "POLARIS_ENDPOINT/POLARIS_MANAGEMENT_ENDPOINT configuration",
        )
    try:
        roles = get_principal_roles(
            catalog_endpoint,
            management_endpoint,
            root_client_id,
            root_client_secret,
            session.principal_name,
        )
    except PolarisClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"principal": session.principal_name, "roles": roles}


@app.get("/access")
def access_route(session: Session = Depends(require_session)) -> dict:
    root_client_id = os.environ.get("POLARIS_ROOT_CLIENT_ID")
    root_client_secret = os.environ.get("POLARIS_ROOT_CLIENT_SECRET")
    catalog_endpoint = os.environ.get("POLARIS_ENDPOINT")
    management_endpoint = os.environ.get("POLARIS_MANAGEMENT_ENDPOINT")
    _, catalog = _catalog_config()
    if not root_client_id or not root_client_secret or not catalog_endpoint or not management_endpoint:
        raise HTTPException(
            status_code=500,
            detail="server missing POLARIS_ROOT_CLIENT_ID/POLARIS_ROOT_CLIENT_SECRET/"
            "POLARIS_ENDPOINT/POLARIS_MANAGEMENT_ENDPOINT configuration",
        )
    try:
        principal_names = list_principals(
            catalog_endpoint, management_endpoint, root_client_id, root_client_secret
        )
        principals = []
        for name in principal_names:
            principal_roles = get_principal_roles(
                catalog_endpoint, management_endpoint, root_client_id, root_client_secret, name
            )
            role_entries = []
            for principal_role in principal_roles:
                catalog_roles = get_catalog_roles_for_principal_role(
                    catalog_endpoint,
                    management_endpoint,
                    root_client_id,
                    root_client_secret,
                    catalog,
                    principal_role,
                )
                catalog_role_entries = []
                for catalog_role in catalog_roles:
                    grants = get_grants_for_catalog_role(
                        catalog_endpoint,
                        management_endpoint,
                        root_client_id,
                        root_client_secret,
                        catalog,
                        catalog_role,
                    )
                    catalog_role_entries.append({"name": catalog_role, "grants": grants})
                role_entries.append(
                    {"name": principal_role, "catalog_roles": catalog_role_entries}
                )
            principals.append({"name": name, "principal_roles": role_entries})
    except PolarisClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"principals": principals}


@app.get("/history")
def history_route(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(require_session),
) -> dict:
    history = get_history(session.principal_name, limit=limit, offset=offset)
    return {"history": history}
