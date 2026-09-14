import app.main as main_module
from fastapi.testclient import TestClient

from app.main import app
from app.polaris_client import PolarisClientError

client = TestClient(app)


def _logged_in_cookie():
    session_id = main_module.create_session("cid", "secret", "loader")
    return {"lakehouse_session": session_id}


def test_namespaces_requires_a_session():
    response = client.get("/catalog/namespaces")
    assert response.status_code == 401


def test_namespaces_returns_list(monkeypatch):
    monkeypatch.setenv("POLARIS_ENDPOINT", "http://polaris:8181/api/catalog")
    monkeypatch.setenv("POLARIS_CATALOG", "lakehouse")
    monkeypatch.setattr(
        main_module,
        "list_namespaces",
        lambda endpoint, cid, secret, catalog: ["nyc_taxi"],
    )

    response = client.get("/catalog/namespaces", cookies=_logged_in_cookie())

    assert response.status_code == 200
    assert response.json() == {"namespaces": ["nyc_taxi"]}


def test_namespaces_uses_the_sessions_own_credentials(monkeypatch):
    monkeypatch.setenv("POLARIS_ENDPOINT", "http://polaris:8181/api/catalog")
    monkeypatch.setenv("POLARIS_CATALOG", "lakehouse")
    captured = {}

    def fake_list_namespaces(endpoint, client_id, client_secret, catalog):
        captured["client_id"] = client_id
        captured["client_secret"] = client_secret
        return []

    monkeypatch.setattr(main_module, "list_namespaces", fake_list_namespaces)

    client.get("/catalog/namespaces", cookies=_logged_in_cookie())

    assert captured == {"client_id": "cid", "client_secret": "secret"}


def test_tables_returns_list(monkeypatch):
    monkeypatch.setenv("POLARIS_ENDPOINT", "http://polaris:8181/api/catalog")
    monkeypatch.setenv("POLARIS_CATALOG", "lakehouse")
    monkeypatch.setattr(
        main_module,
        "list_tables",
        lambda endpoint, cid, secret, catalog, namespace: ["trips", "fct_trips"],
    )

    response = client.get("/catalog/tables/nyc_taxi", cookies=_logged_in_cookie())

    assert response.status_code == 200
    assert response.json() == {"tables": ["trips", "fct_trips"]}


def test_tables_uses_the_sessions_own_credentials_and_correct_namespace(monkeypatch):
    monkeypatch.setenv("POLARIS_ENDPOINT", "http://polaris:8181/api/catalog")
    monkeypatch.setenv("POLARIS_CATALOG", "lakehouse")
    captured = {}

    def fake_list_tables(endpoint, client_id, client_secret, catalog, namespace):
        captured["client_id"] = client_id
        captured["client_secret"] = client_secret
        captured["catalog"] = catalog
        captured["namespace"] = namespace
        return []

    monkeypatch.setattr(main_module, "list_tables", fake_list_tables)

    client.get("/catalog/tables/nyc_taxi", cookies=_logged_in_cookie())

    assert captured == {
        "client_id": "cid",
        "client_secret": "secret",
        "catalog": "lakehouse",
        "namespace": "nyc_taxi",
    }


def test_table_schema_uses_the_sessions_own_credentials_and_correct_args(monkeypatch):
    monkeypatch.setenv("POLARIS_ENDPOINT", "http://polaris:8181/api/catalog")
    monkeypatch.setenv("POLARIS_CATALOG", "lakehouse")
    captured = {}

    def fake_get_table_schema(endpoint, client_id, client_secret, catalog, namespace, table):
        captured["client_id"] = client_id
        captured["client_secret"] = client_secret
        captured["catalog"] = catalog
        captured["namespace"] = namespace
        captured["table"] = table
        return []

    monkeypatch.setattr(main_module, "get_table_schema", fake_get_table_schema)

    client.get("/catalog/tables/nyc_taxi/trips/schema", cookies=_logged_in_cookie())

    assert captured == {
        "client_id": "cid",
        "client_secret": "secret",
        "catalog": "lakehouse",
        "namespace": "nyc_taxi",
        "table": "trips",
    }


def test_table_schema_returns_fields(monkeypatch):
    monkeypatch.setenv("POLARIS_ENDPOINT", "http://polaris:8181/api/catalog")
    monkeypatch.setenv("POLARIS_CATALOG", "lakehouse")
    monkeypatch.setattr(
        main_module,
        "get_table_schema",
        lambda endpoint, cid, secret, catalog, namespace, table: [
            {"name": "VendorID", "type": "int", "required": False}
        ],
    )

    response = client.get(
        "/catalog/tables/nyc_taxi/trips/schema", cookies=_logged_in_cookie()
    )

    assert response.status_code == 200
    assert response.json() == {
        "fields": [{"name": "VendorID", "type": "int", "required": False}]
    }


def test_namespaces_returns_502_on_polaris_client_error(monkeypatch):
    monkeypatch.setenv("POLARIS_ENDPOINT", "http://polaris:8181/api/catalog")
    monkeypatch.setenv("POLARIS_CATALOG", "lakehouse")

    def raise_error(endpoint, cid, secret, catalog):
        raise PolarisClientError("Polaris returned 403 for /v1/lakehouse/namespaces")

    monkeypatch.setattr(main_module, "list_namespaces", raise_error)

    response = client.get("/catalog/namespaces", cookies=_logged_in_cookie())

    assert response.status_code == 502
    assert "403" in response.json()["detail"]
