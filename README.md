# sheetreader

[![tests](https://github.com/5ubh1/sheetreader/actions/workflows/tests.yml/badge.svg)](https://github.com/5ubh1/sheetreader/actions/workflows/tests.yml)

Read Google Sheets into typed Python rows — with retry, token refresh, and a small CLI.

No third-party dependencies. Pure standard library, so it runs anywhere Python does.

```python
from sheetreader import SheetClient

client = SheetClient("token.json")
rows = client.read_records(SHEET_ID, "Sheet1!A1:F100")
# [{'nama': 'Ali', 'kelas': '1A'}, {'nama': 'Budi', 'kelas': '1B'}]
```

## Why this exists

Reading a sheet from a script usually ends up as a bare `urlopen` call that
breaks the first time the API answers `429` or the access token expires.
This package handles the boring parts once:

- **Retry with backoff** on `408`, `429`, `500`, `502`, `503`, `504` — and on network errors.
- **No retry** on `404` or `403` — those are real answers, not blips.
- **Automatic token refresh** on `401`, persisted back to the token file.
- **Row padding** so every row has the same width as the widest one.
- **Readable errors** — a `403` tells you to share the sheet, instead of dumping JSON.

## Install

```bash
pip install -e ".[dev]"
```

Requires Python 3.10+. The only runtime dependency is the standard library.

## Authentication

`sheetreader` reads a Google OAuth 2.0 token file. It understands both shapes
Google writes:

| File shape | Keys used |
|---|---|
Authorized-user file | `client_id`, `client_secret`, `refresh_token`, `access_token` |
Legacy blob | `token`, `refresh_token`, `client_id`, `client_secret` |

When a request comes back `401`, the client refreshes the access token and
retries once. The refreshed token is written back to the file, so the next run
starts with a valid one. Every other field in the file is preserved.

The sheet must be shared with the token's account, or the API answers `403`.

## Library usage

### Read rows as dicts

```python
rows = client.read_records(SHEET_ID, "Sheet1!A1:F100")
for row in rows:
    print(row["nama"], row["kelas"])
```

Blank header cells become `column_1`, `column_2`, … and duplicate headers get a
numeric suffix (`jam`, `jam_2`), so nothing is silently dropped. Fully blank
rows are skipped.

### Read raw rows

```python
rows = client.read_values(SHEET_ID, "Sheet1!A1:F100")
# [['nama', 'kelas'], ['Ali', '1A']]
```

### Read several ranges

```python
data = client.read_many(SHEET_ID, ["Sheet1!A1:B10", "Sheet2!A1:C5"])
```

### Tuning

```python
client = SheetClient(
    "token.json",
    timeout=30.0,   # per-request seconds
    retries=3,      # extra attempts after the first failure
    backoff=1.0,    # base seconds for exponential backoff
)
```

## Command line

```bash
# print a range as JSON objects
sheetreader <SPREADSHEET_ID> "Sheet1!A1:F100" -t token.json

# print raw rows as arrays
sheetreader <SPREADSHEET_ID> "Sheet1!A1:F100" -t token.json --values-only

# print as CSV
sheetreader <SPREADSHEET_ID> "Sheet1!A1:F100" -t token.json -f csv

# use row 2 as the header (0-based)
sheetreader <SPREADSHEET_ID> "Sheet1!A1:F100" -t token.json --header-row 1
```

Exit code is `0` on success and `1` on any handled error, with the reason on
stderr — so it drops into a shell pipeline without extra checks.

## Errors

| Exception | Raised when |
|---|---|
`AuthError` | token file missing or unreadable; refresh rejected |
`ApiError` | access denied (`403`), or a network failure after all retries |
`HttpStatusError` | a non-retryable HTTP status came back |

All of them inherit from `SheetReaderError`, so one `except` catches everything.

```python
from sheetreader import SheetReaderError

try:
    rows = client.read_records(SHEET_ID, "Sheet1!A1:F100")
except SheetReaderError as exc:
    print("could not read the sheet:", exc)
```

## Tests

```bash
python -m pytest
```

The suite covers retry behaviour, token refresh, header handling, and the CLI
without touching the network — every HTTP call is injected.

## License

MIT
