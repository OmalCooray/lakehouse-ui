# lakehouse-ui

A small Snowflake/BigQuery-style query console for a MinIO + Iceberg +
Polaris + DuckDB lakehouse. Log in with a Polaris principal's own
credentials — Polaris's RBAC decides what you can see and do, there's no
separate user system.

Deployed via [`test-k8s-configs`](https://github.com/OmalCooray/test-k8s-configs)
(`charts/lakehouse-ui/`), image published to
`ghcr.io/omalcooray/lakehouse-ui`.

## Features

- **Login** with any Polaris principal's client ID/secret (same mechanism
  Polaris Console uses) — every query runs as that principal, authorized
  by Polaris itself.
- **Catalog browser** — a sidebar tree of namespaces and tables, scoped to
  what your principal can see. Click a table to insert its qualified name
  into the editor.
- **Worksheets** — multiple query tabs, each with its own editor and
  results.
- **Query history** — your own past queries (stored in Postgres, not just
  this browser), click one to reopen it in a new tab.
- **Syntax-highlighted editor** (CodeMirror 5, SQL mode) — highlighting
  only, no autocomplete.

Principal/role/grant management isn't here — that's
[Polaris Console](https://github.com/apache/polaris-tools/tree/main/console)'s
job.

## Run locally

```bash
pip install -r requirements-dev.txt
export POLARIS_ENDPOINT=http://localhost:8181/api/catalog        # kubectl port-forward svc/polaris 8181:8181
export POLARIS_MANAGEMENT_ENDPOINT=http://localhost:8181/api/management
export POLARIS_CATALOG=lakehouse
export POLARIS_ROOT_CLIENT_ID=root
export POLARIS_ROOT_CLIENT_SECRET=<from the polaris-root-credentials Secret>
export LAKEHOUSE_UI_DB_HOST=localhost                              # kubectl port-forward svc/polaris-postgres 5432:5432
export LAKEHOUSE_UI_DB_NAME=lakehouse_ui
export LAKEHOUSE_UI_DB_USER=lakehouse_ui
export LAKEHOUSE_UI_DB_PASSWORD=<from the polaris-postgres Secret, key LAKEHOUSE_UI_USER_PASSWORD>
uvicorn app.main:app --reload
```

Open http://localhost:8000, log in with any Polaris principal's own
client_id/client_secret (e.g. the `loader` or `lakehouse-ui` principals
created by `bootstrap/polaris-setup.sh`).

## Tests

```bash
pytest -v
```

## Environment variables

| Var | Purpose |
|---|---|
| `POLARIS_ENDPOINT` | Polaris Iceberg REST Catalog API base URL |
| `POLARIS_MANAGEMENT_ENDPOINT` | Polaris Management API base URL (principals/roles) — a *different* base path than `POLARIS_ENDPOINT`, not derived from it |
| `POLARIS_CATALOG` | Catalog name to attach (`lakehouse`) |
| `POLARIS_ROOT_CLIENT_ID` / `POLARIS_ROOT_CLIENT_SECRET` | The app's own service credential, used only by `/me` to look up a logged-in principal's roles (a regular principal can't do this for itself) |
| `LAKEHOUSE_UI_DB_HOST` / `_NAME` / `_USER` / `_PASSWORD` | Postgres connection for query history (a dedicated database on the existing `polaris-postgres` instance) |

Per-user Polaris credentials are **not** environment variables — they come
from the login form and live only in the server-side session (in-memory,
`replicas: 1`).
