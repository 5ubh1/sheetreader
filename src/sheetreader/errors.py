"""Exceptions raised by sheetreader."""


class SheetReaderError(Exception):
    """Base class for every error raised by this package."""


class AuthError(SheetReaderError):
    """The token file is missing, unreadable, or cannot be refreshed."""


class HttpStatusError(SheetReaderError):
    """A non-2xx HTTP response came back from the API."""

    def __init__(self, status: int, body: str = "") -> None:
        self.status = status
        self.body = body
        super().__init__(f"HTTP {status}: {body[:200]}")


class ApiError(SheetReaderError):
    """The API answered, but the request was rejected (bad range, no access)."""
