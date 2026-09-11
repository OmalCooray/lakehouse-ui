"""lakehouse-ui: a minimal query UI for the MinIO/Iceberg/Polaris/DuckDB stack."""
from __future__ import annotations

from fastapi import FastAPI

app = FastAPI(title="lakehouse-ui")


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}
