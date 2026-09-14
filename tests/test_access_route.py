import app.main as main_module
from fastapi.testclient import TestClient

from app.main import app
from app.polaris_client import PolarisClientError

client = TestClient(app)


def _logged_in_cookie(principal="loader"):
    session_id = main_module.create_session("cid", "secret", principal)
    return {"lakehouse_session": session_id}


def _set_env(monkeypatch):
    monkeypatch.setenv("POLARIS_ENDPOINT", "http://polaris:8181/api/catalog")
    monkeypatch.setenv("POLARIS_MANAGEMENT_ENDPOINT", "http://polaris:8181/api/management")
    monkeypatch.setenv("POLARIS_CATALOG", "lakehouse")
    monkeypatch.setenv("POLARIS_ROOT_CLIENT_ID", "root")
    monkeypatch.setenv("POLARIS_ROOT_CLIENT_SECRET", "rootsecret")


def test_access_requires_a_session():
    response = client.get("/access")
    assert response.status_code == 401


def test_access_returns_500_when_root_credentials_missing(monkeypatch):
    monkeypatch.setenv("POLARIS_ENDPOINT", "http://polaris:8181/api/catalog")
    monkeypatch.setenv("POLARIS_CATALOG", "lakehouse")
    monkeypatch.delenv("POLARIS_ROOT_CLIENT_ID", raising=False)

    response = client.get("/access", cookies=_logged_in_cookie())

    assert response.status_code == 500


def test_access_returns_the_full_principal_role_grant_chain(monkeypatch):
    _set_env(monkeypatch)
    monkeypatch.setattr(main_module, "list_principals", lambda *a, **kw: ["loader"])
    monkeypatch.setattr(main_module, "get_principal_roles", lambda *a, **kw: ["loader_role"])
    monkeypatch.setattr(
        main_module, "get_catalog_roles_for_principal_role", lambda *a, **kw: ["loader_catalog_role"]
    )
    monkeypatch.setattr(
        main_module, "get_grants_for_catalog_role", lambda *a, **kw: ["CATALOG_MANAGE_CONTENT"]
    )

    response = client.get("/access", cookies=_logged_in_cookie())

    assert response.status_code == 200
    assert response.json() == {
        "principals": [
            {
                "name": "loader",
                "principal_roles": [
                    {
                        "name": "loader_role",
                        "catalog_roles": [
                            {"name": "loader_catalog_role", "grants": ["CATALOG_MANAGE_CONTENT"]}
                        ],
                    }
                ],
            }
        ]
    }


def test_access_returns_502_on_polaris_client_error(monkeypatch):
    _set_env(monkeypatch)

    def raise_error(*a, **kw):
        raise PolarisClientError("not authorized (403)")

    monkeypatch.setattr(main_module, "list_principals", raise_error)

    response = client.get("/access", cookies=_logged_in_cookie())

    assert response.status_code == 502
