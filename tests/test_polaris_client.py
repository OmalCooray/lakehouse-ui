import json
import urllib.error
from unittest.mock import MagicMock

import pytest

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

TOKEN_RESPONSE = {"access_token": "tok"}


def _response(body):
    resp = MagicMock()
    resp.read.return_value = json.dumps(body).encode()
    resp.__enter__.return_value = resp
    return resp


def _fake_urlopen(responses):
    """`responses` maps a URL substring to the JSON body to return for the
    first request whose URL contains that substring."""

    def _urlopen(request, timeout=10):
        url = request.full_url
        for substring, body in responses.items():
            if substring in url:
                return _response(body)
        raise AssertionError(f"unexpected URL: {url}")

    return _urlopen


def test_list_namespaces_returns_names(monkeypatch):
    monkeypatch.setattr(
        "app.polaris_client.urllib.request.urlopen",
        _fake_urlopen(
            {
                "oauth/tokens": TOKEN_RESPONSE,
                "lakehouse/namespaces": {
                    "namespaces": [["nyc_taxi"]],
                    "next-page-token": None,
                },
            }
        ),
    )

    result = list_namespaces("http://polaris:8181/api/catalog", "cid", "secret", "lakehouse")

    assert result == ["nyc_taxi"]


def test_list_tables_returns_names(monkeypatch):
    monkeypatch.setattr(
        "app.polaris_client.urllib.request.urlopen",
        _fake_urlopen(
            {
                "oauth/tokens": TOKEN_RESPONSE,
                "namespaces/nyc_taxi/tables": {
                    "identifiers": [
                        {"namespace": ["nyc_taxi"], "name": "trips"},
                        {"namespace": ["nyc_taxi"], "name": "fct_trips"},
                    ],
                    "next-page-token": None,
                },
            }
        ),
    )

    result = list_tables(
        "http://polaris:8181/api/catalog", "cid", "secret", "lakehouse", "nyc_taxi"
    )

    assert result == ["trips", "fct_trips"]


def test_get_table_schema_returns_fields_from_the_current_schema(monkeypatch):
    monkeypatch.setattr(
        "app.polaris_client.urllib.request.urlopen",
        _fake_urlopen(
            {
                "oauth/tokens": TOKEN_RESPONSE,
                "tables/trips": {
                    "metadata": {
                        "current-schema-id": 0,
                        "schemas": [
                            {
                                "schema-id": 0,
                                "fields": [
                                    {"id": 1, "name": "VendorID", "required": False, "type": "int"},
                                    {"id": 2, "name": "trip_distance", "required": False, "type": "double"},
                                ],
                            }
                        ],
                    }
                },
            }
        ),
    )

    result = get_table_schema(
        "http://polaris:8181/api/catalog", "cid", "secret", "lakehouse", "nyc_taxi", "trips"
    )

    assert result == [
        {"name": "VendorID", "type": "int", "required": False},
        {"name": "trip_distance", "type": "double", "required": False},
    ]


def test_get_table_details_returns_fields_location_and_snapshot(monkeypatch):
    monkeypatch.setattr(
        "app.polaris_client.urllib.request.urlopen",
        _fake_urlopen(
            {
                "oauth/tokens": TOKEN_RESPONSE,
                "namespaces/nyc_taxi/tables/trips": {
                    "metadata": {
                        "location": "s3://lakehouse/nyc_taxi/trips",
                        "last-updated-ms": 1789357660542,
                        "current-schema-id": 0,
                        "current-snapshot-id": 999,
                        "schemas": [
                            {
                                "schema-id": 0,
                                "fields": [
                                    {"name": "VendorID", "type": "int", "required": False},
                                ],
                            }
                        ],
                        "snapshots": [
                            {
                                "snapshot-id": 999,
                                "timestamp-ms": 1789357660542,
                                "summary": {
                                    "operation": "overwrite",
                                    "total-records": "2964624",
                                    "total-data-files": "5",
                                },
                            }
                        ],
                        "properties": {"created-at": "2026-09-14T03:47:19Z"},
                    }
                },
            }
        ),
    )

    result = get_table_details(
        "http://polaris:8181/api/catalog", "cid", "secret", "lakehouse", "nyc_taxi", "trips"
    )

    assert result == {
        "fields": [{"name": "VendorID", "type": "int", "required": False}],
        "location": "s3://lakehouse/nyc_taxi/trips",
        "last_updated_ms": 1789357660542,
        "current_snapshot": {
            "operation": "overwrite",
            "total_records": "2964624",
            "total_data_files": "5",
            "timestamp_ms": 1789357660542,
        },
        "properties": {"created-at": "2026-09-14T03:47:19Z"},
    }


def test_get_table_details_current_snapshot_is_none_for_an_empty_table(monkeypatch):
    monkeypatch.setattr(
        "app.polaris_client.urllib.request.urlopen",
        _fake_urlopen(
            {
                "oauth/tokens": TOKEN_RESPONSE,
                "namespaces/nyc_taxi/tables/empty_table": {
                    "metadata": {
                        "location": "s3://lakehouse/nyc_taxi/empty_table",
                        "last-updated-ms": 123,
                        "current-schema-id": 0,
                        "current-snapshot-id": None,
                        "schemas": [{"schema-id": 0, "fields": []}],
                        "snapshots": [],
                        "properties": {},
                    }
                },
            }
        ),
    )

    result = get_table_details(
        "http://polaris:8181/api/catalog", "cid", "secret", "lakehouse", "nyc_taxi", "empty_table"
    )

    assert result["current_snapshot"] is None
    assert result["fields"] == []


def test_get_namespace_details_returns_properties(monkeypatch):
    monkeypatch.setattr(
        "app.polaris_client.urllib.request.urlopen",
        _fake_urlopen(
            {
                "oauth/tokens": TOKEN_RESPONSE,
                "namespaces/nyc_taxi": {"properties": {"location": "s3://lakehouse/nyc_taxi/"}},
            }
        ),
    )

    result = get_namespace_details(
        "http://polaris:8181/api/catalog", "cid", "secret", "lakehouse", "nyc_taxi"
    )

    assert result == {"properties": {"location": "s3://lakehouse/nyc_taxi/"}}


def test_get_principal_roles_returns_role_names(monkeypatch):
    monkeypatch.setattr(
        "app.polaris_client.urllib.request.urlopen",
        _fake_urlopen(
            {
                "oauth/tokens": TOKEN_RESPONSE,
                "v1/principals/loader/principal-roles": {
                    "roles": [{"name": "loader_role", "federated": False}]
                },
            }
        ),
    )

    result = get_principal_roles(
        "http://polaris:8181/api/catalog",
        "http://polaris:8181/api/management",
        "root",
        "rootsecret",
        "loader",
    )

    assert result == ["loader_role"]


def test_get_principal_roles_uses_the_v1_management_api_path(monkeypatch):
    # Regression test: an earlier version of this function built
    # f"/principals/{name}/principal-roles" (missing the /v1 prefix every
    # other Management/Catalog API path in this module has). Polaris 1.7.0
    # returns a 404 for that path — confirmed live against the real
    # cluster, not assumed. `_fake_urlopen`'s substring matching means the
    # test above would pass even without the prefix, so this test asserts
    # the exact requested URL to pin the correct path going forward.
    requested_urls = []

    def _urlopen(request, timeout=10):
        requested_urls.append(request.full_url)
        if "oauth/tokens" in request.full_url:
            return _response(TOKEN_RESPONSE)
        return _response({"roles": []})

    monkeypatch.setattr("app.polaris_client.urllib.request.urlopen", _urlopen)

    get_principal_roles(
        "http://polaris:8181/api/catalog",
        "http://polaris:8181/api/management",
        "root",
        "rootsecret",
        "loader",
    )

    assert (
        "http://polaris:8181/api/management/v1/principals/loader/principal-roles"
        in requested_urls
    )


def test_get_principal_roles_fetches_its_oauth_token_from_the_catalog_endpoint(monkeypatch):
    # Regression test: get_principal_roles used to fetch its OAuth token
    # from `management_endpoint` (the only endpoint it received). Polaris
    # does not expose /v1/oauth/tokens under the Management API base path
    # — only under the Catalog API base path — confirmed live: a 404. This
    # asserts the token request specifically hits catalog_endpoint, not
    # management_endpoint, so this can't silently regress back to using a
    # single endpoint for both.
    requested_urls = []

    def _urlopen(request, timeout=10):
        requested_urls.append(request.full_url)
        if "oauth/tokens" in request.full_url:
            return _response(TOKEN_RESPONSE)
        return _response({"roles": []})

    monkeypatch.setattr("app.polaris_client.urllib.request.urlopen", _urlopen)

    get_principal_roles(
        "http://polaris:8181/api/catalog",
        "http://polaris:8181/api/management",
        "root",
        "rootsecret",
        "loader",
    )

    assert "http://polaris:8181/api/catalog/v1/oauth/tokens" in requested_urls
    assert "http://polaris:8181/api/management/v1/oauth/tokens" not in requested_urls


def test_list_namespaces_raises_polaris_client_error_on_http_error(monkeypatch):
    def _urlopen(request, timeout=10):
        if "oauth/tokens" in request.full_url:
            return _response(TOKEN_RESPONSE)
        raise urllib.error.HTTPError(request.full_url, 403, "forbidden", {}, None)

    monkeypatch.setattr("app.polaris_client.urllib.request.urlopen", _urlopen)

    with pytest.raises(PolarisClientError, match="403"):
        list_namespaces("http://polaris:8181/api/catalog", "cid", "secret", "lakehouse")


def test_list_namespaces_raises_on_unreachable_polaris(monkeypatch):
    def raise_url_error(request, timeout=10):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr("app.polaris_client.urllib.request.urlopen", raise_url_error)

    with pytest.raises(PolarisClientError, match="could not"):
        list_namespaces("http://polaris:8181/api/catalog", "cid", "secret", "lakehouse")


def _malformed_json_response():
    resp = MagicMock()
    resp.read.return_value = b"not json"
    resp.__enter__.return_value = resp
    return resp


def test_list_namespaces_raises_polaris_client_error_on_malformed_token_response(
    monkeypatch,
):
    def _urlopen(request, timeout=10):
        assert "oauth/tokens" in request.full_url
        return _malformed_json_response()

    monkeypatch.setattr("app.polaris_client.urllib.request.urlopen", _urlopen)

    with pytest.raises(PolarisClientError, match="could not be parsed"):
        list_namespaces("http://polaris:8181/api/catalog", "cid", "secret", "lakehouse")


def test_list_namespaces_raises_polaris_client_error_on_malformed_catalog_response(
    monkeypatch,
):
    def _urlopen(request, timeout=10):
        if "oauth/tokens" in request.full_url:
            return _response(TOKEN_RESPONSE)
        return _malformed_json_response()

    monkeypatch.setattr("app.polaris_client.urllib.request.urlopen", _urlopen)

    with pytest.raises(PolarisClientError, match="could not be parsed"):
        list_namespaces("http://polaris:8181/api/catalog", "cid", "secret", "lakehouse")


def test_list_namespaces_does_not_blame_credentials_for_a_server_error(monkeypatch):
    def _urlopen(request, timeout=10):
        if "oauth/tokens" in request.full_url:
            return _response(TOKEN_RESPONSE)
        raise urllib.error.HTTPError(request.full_url, 503, "service unavailable", {}, None)

    monkeypatch.setattr("app.polaris_client.urllib.request.urlopen", _urlopen)

    with pytest.raises(PolarisClientError) as exc_info:
        list_namespaces("http://polaris:8181/api/catalog", "cid", "secret", "lakehouse")

    assert "authorized" not in str(exc_info.value).lower()


def test_get_token_does_not_blame_credentials_for_a_server_error(monkeypatch):
    def _urlopen(request, timeout=10):
        raise urllib.error.HTTPError(request.full_url, 500, "internal error", {}, None)

    monkeypatch.setattr("app.polaris_client.urllib.request.urlopen", _urlopen)

    with pytest.raises(PolarisClientError) as exc_info:
        list_namespaces("http://polaris:8181/api/catalog", "cid", "secret", "lakehouse")

    assert "invalid client_id or client_secret" not in str(exc_info.value)


def test_list_principals_returns_names(monkeypatch):
    monkeypatch.setattr(
        "app.polaris_client.urllib.request.urlopen",
        _fake_urlopen(
            {
                "oauth/tokens": TOKEN_RESPONSE,
                "principals": {
                    "principals": [{"name": "root"}, {"name": "loader"}, {"name": "lakehouse-ui"}]
                },
            }
        ),
    )

    result = list_principals(
        "http://polaris:8181/api/catalog", "http://polaris:8181/api/management", "root", "rootsecret"
    )

    assert result == ["root", "loader", "lakehouse-ui"]


def test_get_catalog_roles_for_principal_role_returns_names(monkeypatch):
    monkeypatch.setattr(
        "app.polaris_client.urllib.request.urlopen",
        _fake_urlopen(
            {
                "oauth/tokens": TOKEN_RESPONSE,
                "principal-roles/loader_role/catalog-roles/lakehouse": {
                    "roles": [{"name": "loader_catalog_role"}]
                },
            }
        ),
    )

    result = get_catalog_roles_for_principal_role(
        "http://polaris:8181/api/catalog",
        "http://polaris:8181/api/management",
        "root",
        "rootsecret",
        "lakehouse",
        "loader_role",
    )

    assert result == ["loader_catalog_role"]


def test_get_grants_for_catalog_role_returns_privilege_names(monkeypatch):
    monkeypatch.setattr(
        "app.polaris_client.urllib.request.urlopen",
        _fake_urlopen(
            {
                "oauth/tokens": TOKEN_RESPONSE,
                "catalogs/lakehouse/catalog-roles/loader_catalog_role/grants": {
                    "grants": [{"privilege": "CATALOG_MANAGE_CONTENT", "type": "catalog"}]
                },
            }
        ),
    )

    result = get_grants_for_catalog_role(
        "http://polaris:8181/api/catalog",
        "http://polaris:8181/api/management",
        "root",
        "rootsecret",
        "lakehouse",
        "loader_catalog_role",
    )

    assert result == ["CATALOG_MANAGE_CONTENT"]


def test_list_tables_raises_polaris_client_error_on_unexpected_shape(monkeypatch):
    monkeypatch.setattr(
        "app.polaris_client.urllib.request.urlopen",
        _fake_urlopen(
            {
                "oauth/tokens": TOKEN_RESPONSE,
                "namespaces/nyc_taxi/tables": {
                    "identifiers": [{"namespace": ["nyc_taxi"]}],  # missing "name"
                    "next-page-token": None,
                },
            }
        ),
    )

    with pytest.raises(PolarisClientError, match="unexpected response shape"):
        list_tables("http://polaris:8181/api/catalog", "cid", "secret", "lakehouse", "nyc_taxi")
