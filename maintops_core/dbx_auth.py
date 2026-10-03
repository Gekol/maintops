"""Databricks credentials for the web app's REST calls (Manny endpoint, CV upload, ai_parse_document).

With a service principal (DATABRICKS_CLIENT_ID + DATABRICKS_CLIENT_SECRET, OAuth machine-to-machine) the app fetches
its own access token and renews it shortly before it expires, so nobody has to paste tokens. Without one it uses
DATABRICKS_TOKEN as given (a personal access token, or a user's 1-hour OAuth token).
"""

import os
import threading
import time

import requests

RENEW_BEFORE_S = 300                     # fetch a new token this long before the current one expires

_lock = threading.Lock()
_cached = {"token": None, "expires_at": 0.0}


class AuthError(requests.RequestException):
    """No usable Databricks credentials; a RequestException, so callers report the feature as unavailable."""


def _service_principal() -> tuple[str, str] | None:
    cid, secret = os.environ.get("DATABRICKS_CLIENT_ID", ""), os.environ.get("DATABRICKS_CLIENT_SECRET", "")
    return (cid, secret) if cid and secret else None


def bearer() -> str:
    """An access token for the workspace in DATABRICKS_HOST."""
    sp = _service_principal()
    if sp is None:
        return os.environ.get("DATABRICKS_TOKEN", "")
    with _lock:
        if _cached["token"] and time.time() < _cached["expires_at"] - RENEW_BEFORE_S:
            return _cached["token"]
        host = os.environ.get("DATABRICKS_HOST", "").rstrip("/")
        try:
            resp = requests.post(f"{host}/oidc/v1/token", auth=sp, timeout=20,
                                 data={"grant_type": "client_credentials", "scope": "all-apis"})
            body = resp.json() if resp.status_code == 200 else {}
        except (requests.RequestException, ValueError) as exc:
            raise AuthError(f"token request failed: {exc}") from exc
        token, expires_in = body.get("access_token"), body.get("expires_in")
        if not token or not isinstance(expires_in, (int, float)):
            raise AuthError(f"token request returned HTTP {resp.status_code}")
        _cached.update(token=token, expires_at=time.time() + expires_in)
        return token


def headers(content_type: str = "application/json") -> dict:
    return {"Authorization": f"Bearer {bearer()}", "Content-Type": content_type}
