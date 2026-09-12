import app.main as main_module
from fastapi.testclient import TestClient

from app.main import app
from app.polaris_client import PolarisClientError
from app.session import create_session

client = TestClient(app)


def _logged_in_cookie(principal="loader"):
    session_id = create_session("cid", "secret", principal)
    return {"lakehouse_session": session_id}


def _set_env(monkeypatch):
    monkeypatch.setenv("POLARIS_ROOT_CLIENT_ID", "root")
    monkeypatch.setenv("POLARIS_ROOT_CLIENT_SECRET", "rootsecret")
    monkeypatch.setenv("POLARIS_ENDPOINT", "http://polaris:8181/api/catalog")
    monkeypatch.setenv("POLARIS_MANAGEMENT_ENDPOINT", "http://polaris:8181/api/management")


def test_me_requires_a_session():
    response = client.get("/me")
    assert response.status_code == 401


def test_me_returns_principal_and_roles(monkeypatch):
    _set_env(monkeypatch)
    monkeypatch.setattr(
        main_module,
        "get_principal_roles",
        lambda catalog_endpoint, management_endpoint, cid, secret, principal: ["loader_role"],
    )

    response = client.get("/me", cookies=_logged_in_cookie("loader"))

    assert response.status_code == 200
    assert response.json() == {"principal": "loader", "roles": ["loader_role"]}


def test_me_uses_the_root_service_credential_not_the_sessions(monkeypatch):
    _set_env(monkeypatch)
    captured = {}

    def fake_get_principal_roles(catalog_endpoint, management_endpoint, client_id, client_secret, principal):
        captured["client_id"] = client_id
        captured["client_secret"] = client_secret
        return []

    monkeypatch.setattr(main_module, "get_principal_roles", fake_get_principal_roles)

    client.get("/me", cookies=_logged_in_cookie("loader"))

    assert captured == {"client_id": "root", "client_secret": "rootsecret"}


def test_me_passes_both_the_catalog_and_management_endpoints(monkeypatch):
    # Regression test: get_principal_roles needs the Catalog API endpoint
    # for its OAuth token exchange (Polaris has no /v1/oauth/tokens under
    # the Management API base path — confirmed live) as well as the
    # Management API endpoint for the actual roles lookup. Pin both are
    # passed through, and passed as distinct values, not the same one
    # twice.
    _set_env(monkeypatch)
    captured = {}

    def fake_get_principal_roles(catalog_endpoint, management_endpoint, client_id, client_secret, principal):
        captured["catalog_endpoint"] = catalog_endpoint
        captured["management_endpoint"] = management_endpoint
        return []

    monkeypatch.setattr(main_module, "get_principal_roles", fake_get_principal_roles)

    client.get("/me", cookies=_logged_in_cookie("loader"))

    assert captured == {
        "catalog_endpoint": "http://polaris:8181/api/catalog",
        "management_endpoint": "http://polaris:8181/api/management",
    }


def test_me_returns_500_when_root_credentials_missing(monkeypatch):
    monkeypatch.delenv("POLARIS_ROOT_CLIENT_ID", raising=False)
    monkeypatch.delenv("POLARIS_ROOT_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("POLARIS_ENDPOINT", raising=False)
    monkeypatch.delenv("POLARIS_MANAGEMENT_ENDPOINT", raising=False)

    response = client.get("/me", cookies=_logged_in_cookie())

    assert response.status_code == 500


def test_me_returns_502_on_polaris_client_error(monkeypatch):
    _set_env(monkeypatch)

    def raise_error(catalog_endpoint, management_endpoint, cid, secret, principal):
        raise PolarisClientError("Polaris returned 404")

    monkeypatch.setattr(main_module, "get_principal_roles", raise_error)

    response = client.get("/me", cookies=_logged_in_cookie())

    assert response.status_code == 502
