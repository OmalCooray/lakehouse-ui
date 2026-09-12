"""Exchange a Polaris principal's credentials for an access token, and
decode the principal name out of it — used only at login time.

Confirmed live against a real Polaris deployment: the token's JWT payload
carries `sub` (the principal name), `principalId`, `client_id`, `scope` —
no role list. Signature verification is deliberately skipped: we received
this token directly from Polaris over the connection we're about to use
it on, not a token presented by a third party, so there's nothing to
verify against.
"""
from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request


class LoginError(RuntimeError):
    """Raised when the given credentials are rejected by Polaris, or the
    response can't be used to identify the principal."""


def _decode_jwt_payload(token: str) -> dict:
    payload_segment = token.split(".")[1]
    padded = payload_segment + "=" * (-len(payload_segment) % 4)
    return json.loads(base64.urlsafe_b64decode(padded))


def login(polaris_endpoint: str, client_id: str, client_secret: str) -> str:
    """Exchange credentials for a token, return the principal name (the
    token's `sub` claim). Raises LoginError on invalid credentials, an
    unreachable Polaris, or a response that can't be used to identify the
    principal.
    """
    data = urllib.parse.urlencode(
        {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": "PRINCIPAL_ROLE:ALL",
        }
    ).encode()
    request = urllib.request.Request(
        f"{polaris_endpoint}/v1/oauth/tokens",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            body = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raise LoginError("invalid client_id or client_secret") from exc
    except urllib.error.URLError as exc:
        raise LoginError(f"could not reach Polaris: {exc}") from exc

    access_token = body.get("access_token")
    if not access_token:
        raise LoginError("Polaris did not return an access_token")

    claims = _decode_jwt_payload(access_token)
    principal_name = claims.get("sub")
    if not principal_name:
        raise LoginError("token had no 'sub' claim")
    return principal_name
