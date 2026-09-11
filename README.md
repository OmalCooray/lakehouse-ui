# lakehouse-ui

A minimal, open-source query UI for a MinIO + Iceberg + Polaris + DuckDB
lakehouse: one SQL box, one results table. Read-only.

Deployed via [`test-k8s-configs`](https://github.com/OmalCooray/test-k8s-configs)
(`charts/lakehouse-ui/`), image published to
`ghcr.io/omalcooray/lakehouse-ui`.

## Run locally

```bash
pip install -r requirements-dev.txt
export POLARIS_ENDPOINT=http://localhost:8181/api/catalog   # kubectl port-forward svc/polaris 8181:8181
export POLARIS_CLIENT_ID=lakehouse-ui
export POLARIS_CLIENT_SECRET=<from the lakehouse-ui-polaris-credentials Secret>
export POLARIS_CATALOG=lakehouse
uvicorn app.main:app --reload
```

Open http://localhost:8000.

## Tests

```bash
pytest -v
```

## Environment variables

| Var | Purpose |
|---|---|
| `POLARIS_ENDPOINT` | Polaris REST catalog API base URL |
| `POLARIS_CLIENT_ID` / `POLARIS_CLIENT_SECRET` | Scoped principal credentials (read-only on the `lakehouse` catalog) |
| `POLARIS_CATALOG` | Catalog name to attach (`lakehouse`) |
