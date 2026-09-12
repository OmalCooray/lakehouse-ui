import json
import urllib.error
from unittest.mock import MagicMock

import pytest

from app.polaris_auth import LoginError, login

# A real Polaris token has 3 dot-separated base64url segments; only the
# payload (middle) segment matters here. These were generated the same way
# and decode to {"sub": "loader"} / {"foo": "bar"} respectively — verified
# against app.polaris_auth's own decode logic before being pasted in here.
TOKEN_WITH_SUB = "eyJhbGciOiAiUlMyNTYiLCAidHlwIjogIkpXVCJ9.eyJzdWIiOiAibG9hZGVyIn0.sig"
TOKEN_WITHOUT_SUB = "eyJhbGciOiAiUlMyNTYiLCAidHlwIjogIkpXVCJ9.eyJmb28iOiAiYmFyIn0.sig"


def _fake_response(body: dict):
    response = MagicMock()
    response.read.return_value = json.dumps(body).encode()
    response.__enter__.return_value = response
    return response


def test_login_returns_principal_name_from_sub_claim(monkeypatch):
    fake = _fake_response({"access_token": TOKEN_WITH_SUB})
    monkeypatch.setattr(
        "app.polaris_auth.urllib.request.urlopen", lambda req, timeout=10: fake
    )

    principal = login("http://polaris:8181/api/catalog", "cid", "secret")

    assert principal == "loader"


def test_login_raises_on_http_error(monkeypatch):
    def raise_http_error(req, timeout=10):
        raise urllib.error.HTTPError("url", 401, "unauthorized", {}, None)

    monkeypatch.setattr("app.polaris_auth.urllib.request.urlopen", raise_http_error)

    with pytest.raises(LoginError, match="invalid client_id or client_secret"):
        login("http://polaris:8181/api/catalog", "cid", "wrong")


def test_login_raises_when_polaris_unreachable(monkeypatch):
    def raise_url_error(req, timeout=10):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr("app.polaris_auth.urllib.request.urlopen", raise_url_error)

    with pytest.raises(LoginError, match="could not reach Polaris"):
        login("http://polaris:8181/api/catalog", "cid", "secret")


def test_login_raises_when_no_access_token_in_response(monkeypatch):
    fake = _fake_response({"error": "nope"})
    monkeypatch.setattr(
        "app.polaris_auth.urllib.request.urlopen", lambda req, timeout=10: fake
    )

    with pytest.raises(LoginError, match="access_token"):
        login("http://polaris:8181/api/catalog", "cid", "secret")


def test_login_raises_when_token_has_no_sub_claim(monkeypatch):
    fake = _fake_response({"access_token": TOKEN_WITHOUT_SUB})
    monkeypatch.setattr(
        "app.polaris_auth.urllib.request.urlopen", lambda req, timeout=10: fake
    )

    with pytest.raises(LoginError, match="sub"):
        login("http://polaris:8181/api/catalog", "cid", "secret")
