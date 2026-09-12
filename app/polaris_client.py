"""Thin REST client for Polaris's Iceberg Catalog API (namespaces, tables,
table schemas) and Management API (principal role lookups).

Every call does its own fresh OAuth exchange with the given credentials —
no token caching. Polaris's default token lifetime is 1 hour; a
long-lived session shouldn't silently break because a cached token expired,
and the extra HTTP round trip per call is cheap for how infrequently these
are called (sidebar browsing, not the hot query path).

Response shapes below were confirmed against a real Polaris 1.7.0
deployment, not assumed from docs:
  namespaces: {"namespaces": [["nyc_taxi"]], "next-page-token": null}
  tables:     {"identifiers": [{"namespace": [...], "name": "trips"}], ...}
  schema:     {"metadata": {"current-schema-id": 0, "schemas": [
                 {"schema-id": 0, "fields": [{"name":..,"type":..,"required":..}]}
               ]}}
  roles:      {"roles": [{"name": "loader_role", ...}]}
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request


class PolarisClientError(RuntimeError):
    """Raised when a Polaris REST call fails (auth, network, or non-2xx)."""


def _get_token(base_url: str, client_id: str, client_secret: str) -> str:
    data = urllib.parse.urlencode(
        {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": "PRINCIPAL_ROLE:ALL",
        }
    ).encode()
    request = urllib.request.Request(
        f"{base_url}/v1/oauth/tokens",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            body = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise PolarisClientError("invalid client_id or client_secret") from exc
        raise PolarisClientError(
            f"Polaris returned an error ({exc.code}) authenticating"
        ) from exc
    except urllib.error.URLError as exc:
        raise PolarisClientError(f"could not authenticate with Polaris: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise PolarisClientError(
            "Polaris returned a response that could not be parsed as JSON"
        ) from exc
    token = body.get("access_token")
    if not token:
        raise PolarisClientError("Polaris did not return an access_token")
    return token


def _get(base_url: str, token: str, path: str) -> dict:
    request = urllib.request.Request(
        f"{base_url}{path}",
        headers={"Authorization": f"Bearer {token}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise PolarisClientError(f"not authorized for {path} ({exc.code})") from exc
        raise PolarisClientError(f"Polaris returned {exc.code} for {path}") from exc
    except urllib.error.URLError as exc:
        raise PolarisClientError(f"could not reach Polaris: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise PolarisClientError(
            f"Polaris returned a response that could not be parsed as JSON for {path}"
        ) from exc


def list_namespaces(
    catalog_endpoint: str, client_id: str, client_secret: str, catalog: str
) -> list[str]:
    """Return the top-level namespace names in `catalog`."""
    token = _get_token(catalog_endpoint, client_id, client_secret)
    body = _get(catalog_endpoint, token, f"/v1/{catalog}/namespaces")
    return [".".join(parts) for parts in body.get("namespaces", [])]


def list_tables(
    catalog_endpoint: str, client_id: str, client_secret: str, catalog: str, namespace: str
) -> list[str]:
    """Return table names in `catalog`.`namespace`."""
    token = _get_token(catalog_endpoint, client_id, client_secret)
    body = _get(catalog_endpoint, token, f"/v1/{catalog}/namespaces/{namespace}/tables")
    try:
        return [identifier["name"] for identifier in body.get("identifiers", [])]
    except (KeyError, TypeError) as exc:
        raise PolarisClientError(f"unexpected response shape from Polaris: {exc}") from exc


def get_table_schema(
    catalog_endpoint: str,
    client_id: str,
    client_secret: str,
    catalog: str,
    namespace: str,
    table: str,
) -> list[dict]:
    """Return the table's current schema as a list of
    {"name", "type", "required"} dicts."""
    token = _get_token(catalog_endpoint, client_id, client_secret)
    body = _get(
        catalog_endpoint, token, f"/v1/{catalog}/namespaces/{namespace}/tables/{table}"
    )
    metadata = body.get("metadata", {})
    schemas = metadata.get("schemas", [])
    current_id = metadata.get("current-schema-id")
    schema = next(
        (s for s in schemas if s.get("schema-id") == current_id),
        schemas[0] if schemas else {},
    )
    try:
        return [
            {"name": f["name"], "type": f["type"], "required": f["required"]}
            for f in schema.get("fields", [])
        ]
    except (KeyError, TypeError) as exc:
        raise PolarisClientError(f"unexpected response shape from Polaris: {exc}") from exc


def get_principal_roles(
    catalog_endpoint: str,
    management_endpoint: str,
    client_id: str,
    client_secret: str,
    principal_name: str,
) -> list[str]:
    """Return the principal role names assigned to `principal_name`.

    `client_id`/`client_secret` here are a credential authorized to look up
    *other* principals' roles (a regular principal is not authorized to
    list even its own — confirmed live) — in practice this is always
    called with the app's own root service credential, never a session's.

    Polaris's OAuth token endpoint only exists under the Catalog API base
    path (`{catalog_endpoint}/v1/oauth/tokens`) — the Management API base
    path does NOT expose its own `/v1/oauth/tokens` (confirmed live: a
    404). So the token is fetched from `catalog_endpoint`, then used as a
    Bearer token against `management_endpoint`'s own paths, exactly like a
    real client would use one OAuth server for two separate resource
    APIs.
    """
    token = _get_token(catalog_endpoint, client_id, client_secret)
    body = _get(
        management_endpoint, token, f"/v1/principals/{principal_name}/principal-roles"
    )
    try:
        return [role["name"] for role in body.get("roles", [])]
    except (KeyError, TypeError) as exc:
        raise PolarisClientError(f"unexpected response shape from Polaris: {exc}") from exc
