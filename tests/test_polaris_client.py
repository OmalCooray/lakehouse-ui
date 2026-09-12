import json
import urllib.error
from unittest.mock import MagicMock

import pytest

from app.polaris_client import (
    PolarisClientError,
    get_principal_roles,
    get_table_schema,
    list_namespaces,
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


def test_get_principal_roles_returns_role_names(monkeypatch):
    monkeypatch.setattr(
        "app.polaris_client.urllib.request.urlopen",
        _fake_urlopen(
            {
                "oauth/tokens": TOKEN_RESPONSE,
                "principals/loader/principal-roles": {
                    "roles": [{"name": "loader_role", "federated": False}]
                },
            }
        ),
    )

    result = get_principal_roles(
        "http://polaris:8181/api/management", "root", "rootsecret", "loader"
    )

    assert result == ["loader_role"]


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
