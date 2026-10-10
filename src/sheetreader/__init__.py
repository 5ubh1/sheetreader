"""sheetreader — read Google Sheets into typed Python rows.

Small, dependency-free reader for the Google Sheets REST API with
retry, token refresh, and a command line interface.

Typical use::

    from sheetreader import SheetClient

    client = SheetClient("token.json")
    rows = client.read_records(SHEET_ID, "Sheet1!A1:F100")
"""

from .auth import Token, get_access_token, load_token, refresh_token
from .client import SheetClient, parse_values, rows_to_records
from .errors import ApiError, AuthError, HttpStatusError, SheetReaderError

__version__ = "0.1.0"

__all__ = [
    "SheetClient",
    "Token",
    "load_token",
    "refresh_token",
    "get_access_token",
    "parse_values",
    "rows_to_records",
    "SheetReaderError",
    "AuthError",
    "ApiError",
    "HttpStatusError",
    "__version__",
]
