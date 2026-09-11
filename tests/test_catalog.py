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
    "POLARIS_CLIENT_ID": "lakehouse-ui",
    "POLARIS_CLIENT_SECRET": "s3cr3t",
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
        build_connection(FakeConnection())


def test_build_connection_requires_client_id(monkeypatch):
    _set_env(monkeypatch, POLARIS_CLIENT_ID=None)
    with pytest.raises(CatalogConfigError, match="POLARIS_CLIENT_ID"):
        build_connection(FakeConnection())


def test_build_connection_installs_extensions_and_attaches(monkeypatch):
    _set_env(monkeypatch)
    fake = FakeConnection()

    result = build_connection(fake)

    assert result is fake
    joined = "\n".join(fake.executed)
    assert "INSTALL iceberg" in joined
    assert "LOAD iceberg" in joined
    assert "INSTALL httpfs" in joined
    assert "LOAD httpfs" in joined
    assert "CLIENT_ID 'lakehouse-ui'" in joined
    assert "CLIENT_SECRET 's3cr3t'" in joined
    assert "ATTACH 'lakehouse' AS lakehouse" in joined
    assert (
        "ENDPOINT 'http://polaris.lakehouse.svc.cluster.local:8181/api/catalog'"
        in joined
    )
    assert "ACCESS_DELEGATION_MODE 'vended_credentials'" in joined


def test_build_connection_escapes_single_quotes_in_secret(monkeypatch):
    _set_env(monkeypatch, POLARIS_CLIENT_SECRET="o'brien")
    fake = FakeConnection()

    build_connection(fake)

    assert "CLIENT_SECRET 'o''brien'" in "\n".join(fake.executed)
