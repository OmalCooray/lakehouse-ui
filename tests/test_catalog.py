import sys
import types

import pytest

from app.catalog import CatalogConfigError, build_connection


class FakeConnection:
    def __init__(self):
        self.executed = []

    def execute(self, sql):
        self.executed.append(sql)
        return self


REQUIRED_ENV = {
    "POLARIS_ENDPOINT": "http://polaris.lakehouse.svc.cluster.local:8181/api/catalog",
    "POLARIS_CATALOG": "lakehouse",
}


def _set_env(monkeypatch, **overrides):
    values = {**REQUIRED_ENV, **overrides}
    for key, value in values.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)


def test_build_connection_requires_polaris_endpoint(monkeypatch):
    _set_env(monkeypatch, POLARIS_ENDPOINT=None)
    with pytest.raises(CatalogConfigError, match="POLARIS_ENDPOINT"):
        build_connection("lakehouse-ui", "s3cr3t", conn=FakeConnection())


def test_build_connection_requires_catalog(monkeypatch):
    _set_env(monkeypatch, POLARIS_CATALOG=None)
    with pytest.raises(CatalogConfigError, match="POLARIS_CATALOG"):
        build_connection("lakehouse-ui", "s3cr3t", conn=FakeConnection())


def test_build_connection_installs_extensions_and_attaches(monkeypatch):
    _set_env(monkeypatch)
    fake = FakeConnection()

    result = build_connection("lakehouse-ui", "s3cr3t", conn=fake)

    assert result is fake
    assert fake.executed == [
        "INSTALL iceberg",
        "LOAD iceberg",
        "INSTALL httpfs",
        "LOAD httpfs",
        "CREATE OR REPLACE SECRET polaris_secret ("
        "TYPE iceberg, "
        "CLIENT_ID 'lakehouse-ui', "
        "CLIENT_SECRET 's3cr3t', "
        "ENDPOINT 'http://polaris.lakehouse.svc.cluster.local:8181/api/catalog'"
        ")",
        "ATTACH 'lakehouse' AS \"lakehouse\" ("
        "TYPE iceberg, "
        "ENDPOINT 'http://polaris.lakehouse.svc.cluster.local:8181/api/catalog', "
        "ACCESS_DELEGATION_MODE 'vended_credentials'"
        ")",
    ]


def test_build_connection_escapes_single_quotes_in_secret(monkeypatch):
    _set_env(monkeypatch)
    fake = FakeConnection()

    build_connection("lakehouse-ui", "o'brien", conn=fake)

    assert "CLIENT_SECRET 'o''brien'" in "\n".join(fake.executed)


def test_build_connection_opens_in_memory_duckdb_when_no_conn_given(monkeypatch):
    _set_env(monkeypatch)
    fake = FakeConnection()
    calls = []

    def fake_connect(path):
        calls.append(path)
        return fake

    fake_duckdb_module = types.SimpleNamespace(connect=fake_connect)
    monkeypatch.setitem(sys.modules, "duckdb", fake_duckdb_module)

    result = build_connection("lakehouse-ui", "s3cr3t")

    assert result is fake
    assert calls == [":memory:"]
    assert "INSTALL iceberg" in "\n".join(fake.executed)


def test_build_connection_rejects_empty_client_id(monkeypatch):
    _set_env(monkeypatch)
    with pytest.raises(CatalogConfigError, match="client_id"):
        build_connection("", "s3cr3t", conn=FakeConnection())


def test_build_connection_rejects_empty_client_secret(monkeypatch):
    _set_env(monkeypatch)
    with pytest.raises(CatalogConfigError, match="client_secret"):
        build_connection("lakehouse-ui", "", conn=FakeConnection())


def test_build_connection_rejects_none_client_id(monkeypatch):
    _set_env(monkeypatch)
    with pytest.raises(CatalogConfigError, match="client_id"):
        build_connection(None, "s3cr3t", conn=FakeConnection())
