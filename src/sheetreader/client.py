"""The HTTP client: read ranges, retry on transient failures, map rows to dicts."""

from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Iterable, Sequence

from .auth import get_access_token, load_token, refresh_token, write_token
from .errors import ApiError, AuthError, HttpStatusError

SHEETS_API = "https://sheets.googleapis.com/v4/spreadsheets"

#: Statuses worth trying again: rate limits and transient server errors.
RETRY_STATUSES = frozenset({408, 429, 500, 502, 503, 504})


def parse_values(payload: dict[str, Any]) -> list[list[str]]:
    """Turn an API ``values`` payload into a list of string rows.

    Missing cells come back as empty strings, so every row in the result
    has the same width as the widest row the API returned.
    """
    values = payload.get("values") or []
    rows: list[list[str]] = []
    for row in values:
        if not isinstance(row, list):
            continue
        rows.append(["" if c is None else str(c) for c in row])
    if not rows:
        return []
    width = max(len(r) for r in rows)
    return [r + [""] * (width - len(r)) for r in rows]


def rows_to_records(
    rows: Sequence[Sequence[str]], header_row: int = 0
) -> list[dict[str, str]]:
    """Map a parsed sheet to dicts keyed by the header row.

    Blank header cells become ``column_1``, ``column_2`` ... so nothing is
    silently dropped. Duplicate headers get a numeric suffix.
    """
    if not rows or header_row >= len(rows):
        return []

    raw_header = list(rows[header_row])
    header: list[str] = []
    seen: dict[str, int] = {}
    for i, name in enumerate(raw_header):
        key = (name or "").strip() or f"column_{i + 1}"
        if key in seen:
            seen[key] += 1
            key = f"{key}_{seen[key]}"
        else:
            seen[key] = 1
        header.append(key)

    records: list[dict[str, str]] = []
    for row in rows[header_row + 1 :]:
        if not any(str(c).strip() for c in row):
            continue
        records.append(
            {header[i]: (row[i] if i < len(row) else "") for i in range(len(header))}
        )
    return records


class SheetClient:
    """Read values from Google Sheets.

    Args:
        token_file: path to a Google OAuth token JSON file.
        timeout: per-request timeout in seconds.
        retries: how many extra attempts after the first one fails.
        backoff: base seconds for exponential backoff between attempts.
        opener: injection point for tests; defaults to ``urllib.request.urlopen``.
        sleeper: injection point for tests; defaults to ``time.sleep``.
    """

    def __init__(
        self,
        token_file: str,
        *,
        timeout: float = 30.0,
        retries: int = 3,
        backoff: float = 1.0,
        opener: Any = None,
        sleeper: Any = None,
    ) -> None:
        self.token_file = token_file
        self.timeout = timeout
        self.retries = max(0, retries)
        self.backoff = backoff
        self._opener = opener or urllib.request.urlopen
        self._sleep = sleeper or time.sleep
        self._access_token = ""

    # -- auth ---------------------------------------------------------------
    @property
    def access_token(self) -> str:
        if not self._access_token:
            self._access_token = get_access_token(self.token_file)
        return self._access_token

    def _renew(self) -> None:
        """Force a refresh and persist it, so the next run starts valid."""
        token = load_token(self.token_file)
        refresh_token(token)
        write_token(self.token_file, token)
        self._access_token = token.access_token

    def _url(self, spreadsheet_id: str, range_: str) -> str:
        quoted = urllib.parse.quote(range_, safe="")
        return f"{SHEETS_API}/{spreadsheet_id}/values/{quoted}"

    # -- reading ------------------------------------------------------------
    def read_raw(self, spreadsheet_id: str, range_: str) -> dict[str, Any]:
        """Read one range and return the decoded JSON payload.

        Retries on 429/5xx and on network errors, with exponential backoff
        and a little jitter. Raises immediately on 401/403/404.
        """
        url = self._url(spreadsheet_id, range_)
        last_error: Exception | None = None

        for attempt in range(self.retries + 1):
            req = urllib.request.Request(
                url,
                headers={
                    "Authorization": f"Bearer {self.access_token}",
                    "Accept": "application/json",
                },
            )
            try:
                with self._opener(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", "replace")
                if exc.code == 401:
                    # Token went stale mid-run: refresh once, then retry.
                    try:
                        self._renew()
                    except AuthError:
                        raise AuthError(
                            f"unauthorized and refresh failed: {body[:200]}"
                        ) from exc
                    if attempt < self.retries:
                        continue
                    raise AuthError(f"unauthorized after refresh: {body[:200]}") from exc
                if exc.code == 403:
                    raise ApiError(
                        f"access denied — share the sheet with the token's account: {body[:200]}"
                    ) from exc
                if exc.code not in RETRY_STATUSES:
                    raise HttpStatusError(exc.code, body) from exc
                last_error = HttpStatusError(exc.code, body)
            except urllib.error.URLError as exc:
                last_error = ApiError(f"network error: {exc.reason}")

            if attempt < self.retries:
                delay = self.backoff * (2**attempt) + random.uniform(0, 0.25)
                self._sleep(delay)

        raise last_error or ApiError("request failed with no recorded error")

    def read_values(self, spreadsheet_id: str, range_: str) -> list[list[str]]:
        """Read one range and return normalised string rows."""
        return parse_values(self.read_raw(spreadsheet_id, range_))

    def read_records(
        self, spreadsheet_id: str, range_: str, *, header_row: int = 0
    ) -> list[dict[str, str]]:
        """Read one range and return rows as dicts keyed by the header row."""
        return rows_to_records(
            self.read_values(spreadsheet_id, range_), header_row=header_row
        )

    def read_many(self, spreadsheet_id: str, ranges: Iterable[str]) -> dict[str, list[list[str]]]:
        """Read several ranges from one spreadsheet, in order."""
        return {r: self.read_values(spreadsheet_id, r) for r in ranges}
