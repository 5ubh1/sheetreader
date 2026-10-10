"""Token loading and refresh for Google OAuth 2.0 (installed-app tokens).

Handles the two shapes a token file usually has: an authorized-user file
(``client_id`` / ``refresh_token``) and a raw ``access_token`` blob.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .errors import AuthError

TOKEN_URI = "https://oauth2.googleapis.com/token"


@dataclass
class Token:
    """The fields this package needs out of a Google token file."""

    access_token: str = ""
    refresh_token: str = ""
    client_id: str = ""
    client_secret: str = ""
    token_uri: str = TOKEN_URI

    @property
    def can_refresh(self) -> bool:
        return bool(self.refresh_token and self.client_id and self.client_secret)


def load_token(path: str | Path) -> Token:
    """Read a Google token JSON file into a :class:`Token`.

    Raises:
        AuthError: the file is missing, unreadable, or not valid JSON.
    """
    p = Path(path)
    if not p.is_file():
        raise AuthError(f"token file not found: {p}")
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AuthError(f"token file is not valid JSON: {p}") from exc
    if not isinstance(raw, dict):
        raise AuthError(f"token file must contain a JSON object: {p}")

    return Token(
        # Google writes "token" in some files and "access_token" in others.
        access_token=raw.get("access_token") or raw.get("token") or "",
        refresh_token=raw.get("refresh_token") or "",
        client_id=raw.get("client_id") or "",
        client_secret=raw.get("client_secret") or "",
        token_uri=raw.get("token_uri") or TOKEN_URI,
    )


def write_token(path: str | Path, token: Token) -> None:
    """Persist a refreshed access token back to the token file.

    Keeps every other field in the file untouched, so a refresh never
    destroys scopes or client credentials.
    """
    p = Path(path)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    raw["access_token"] = token.access_token
    # Keep the legacy key in step when the file already used it.
    if "token" in raw:
        raw["token"] = token.access_token
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(raw, indent=2), encoding="utf-8")
    tmp.replace(p)


def refresh_token(token: Token, timeout: float = 30.0) -> str:
    """Exchange a refresh token for a fresh access token.

    Returns the new access token. The caller decides whether to persist it.
    """
    if not token.can_refresh:
        raise AuthError(
            "cannot refresh: token file needs refresh_token, client_id "
            "and client_secret"
        )

    body = urllib.parse.urlencode(
        {
            "client_id": token.client_id,
            "client_secret": token.client_secret,
            "refresh_token": token.refresh_token,
            "grant_type": "refresh_token",
        }
    ).encode()

    req = urllib.request.Request(
        token.token_uri,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise AuthError(f"token refresh rejected (HTTP {exc.code}): {detail}") from exc
    except urllib.error.URLError as exc:
        raise AuthError(f"token refresh failed: {exc.reason}") from exc

    access = payload.get("access_token")
    if not access:
        raise AuthError("token refresh returned no access_token")
    token.access_token = access
    return access


def get_access_token(path: str | Path) -> str:
    """Load a token file and return a usable access token.

    Refreshes only when the file has no access token at all, so a valid
    short-lived token is not spent on a needless round trip.
    """
    token = load_token(path)
    if token.access_token:
        return token.access_token
    return refresh_token(token)
