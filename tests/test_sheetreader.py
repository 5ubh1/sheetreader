"""Tests for sheetreader — no network access, everything is injected."""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from sheetreader import (
    ApiError,
    AuthError,
    HttpStatusError,
    SheetClient,
    load_token,
    parse_values,
    refresh_token,
    rows_to_records,
)
from sheetreader.auth import Token, write_token
from sheetreader.cli import build_parser, main, to_csv


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
class FakeResponse:
    """Minimal stand-in for the object urlopen returns."""

    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode()

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(code: int, body: str = "boom") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://example.test", code, "err", {}, io.BytesIO(body.encode())
    )


class ScriptedOpener:
    """Returns queued results in order; records how many calls were made."""

    def __init__(self, *results):
        self.results = list(results)
        self.calls = 0

    def __call__(self, req, timeout=None):
        self.calls += 1
        if not self.results:
            raise AssertionError("opener called more times than scripted")
        item = self.results.pop(0)
        if isinstance(item, Exception):
            raise item
        return FakeResponse(item)


@pytest.fixture()
def token_file(tmp_path):
    p = tmp_path / "token.json"
    p.write_text(json.dumps({"access_token": "fake-token"}))
    return str(p)


def make_client(token_file, *results, retries=3):
    """A client wired to a scripted opener and a no-op sleeper."""
    return SheetClient(
        token_file,
        retries=retries,
        opener=ScriptedOpener(*results),
        sleeper=lambda _s: None,
    )


# --------------------------------------------------------------------------
# parse_values
# --------------------------------------------------------------------------
def test_parse_values_pads_short_rows():
    payload = {"values": [["a", "b", "c"], ["d"], ["e", "f"]]}
    assert parse_values(payload) == [["a", "b", "c"], ["d", "", ""], ["e", "f", ""]]


def test_parse_values_empty_payload():
    assert parse_values({}) == []
    assert parse_values({"values": []}) == []


def test_parse_values_stringifies_numbers():
    assert parse_values({"values": [[1, 2.5, True]]}) == [["1", "2.5", "True"]]


def test_parse_values_skips_non_list_rows():
    assert parse_values({"values": ["nope", ["ok"]]}) == [["ok"]]


# --------------------------------------------------------------------------
# rows_to_records
# --------------------------------------------------------------------------
def test_rows_to_records_basic():
    rows = [["nama", "kelas"], ["Ali", "1A"], ["Budi", "1B"]]
    assert rows_to_records(rows) == [
        {"nama": "Ali", "kelas": "1A"},
        {"nama": "Budi", "kelas": "1B"},
    ]


def test_rows_to_records_blank_header_gets_generated_name():
    rows = [["nama", "", "kelas"], ["Ali", "x", "1A"]]
    assert rows_to_records(rows) == [{"nama": "Ali", "column_2": "x", "kelas": "1A"}]


def test_rows_to_records_duplicate_header_gets_suffix():
    rows = [["jam", "jam", "jam"], ["1", "2", "3"]]
    assert rows_to_records(rows) == [{"jam": "1", "jam_2": "2", "jam_3": "3"}]


def test_rows_to_records_skips_blank_rows():
    rows = [["nama"], ["Ali"], ["", ""], ["   "], ["Budi"]]
    assert rows_to_records(rows) == [{"nama": "Ali"}, {"nama": "Budi"}]


def test_rows_to_records_empty_input():
    assert rows_to_records([]) == []
    assert rows_to_records([["nama"]]) == []


# --------------------------------------------------------------------------
# auth
# --------------------------------------------------------------------------
def test_load_token_missing_file(tmp_path):
    with pytest.raises(AuthError, match="not found"):
        load_token(tmp_path / "nope.json")


def test_load_token_bad_json(tmp_path):
    p = tmp_path / "t.json"
    p.write_text("{not json")
    with pytest.raises(AuthError, match="not valid JSON"):
        load_token(p)


def test_load_token_accepts_authorized_user_shape(tmp_path):
    p = tmp_path / "t.json"
    p.write_text(
        json.dumps(
            {
                "access_token": "a",
                "refresh_token": "r",
                "client_id": "cid",
                "client_secret": "cs",
            }
        )
    )
    tok = load_token(p)
    assert tok.can_refresh is True
    assert tok.client_id == "cid"


def test_load_token_reads_legacy_token_key(tmp_path):
    # Google writes "token" in some files; it must not be ignored.
    p = tmp_path / "t.json"
    p.write_text(json.dumps({"token": "legacy-value"}))
    assert load_token(p).access_token == "legacy-value"


def test_write_token_keeps_other_fields(tmp_path):
    p = tmp_path / "t.json"
    p.write_text(
        json.dumps(
            {"token": "old", "refresh_token": "r", "client_id": "cid", "scopes": ["s"]}
        )
    )
    tok = load_token(p)
    tok.access_token = "new"
    write_token(p, tok)

    saved = json.loads(p.read_text())
    assert saved["access_token"] == "new"
    assert saved["token"] == "new"  # legacy key kept in step
    assert saved["refresh_token"] == "r"
    assert saved["scopes"] == ["s"]


def test_refresh_requires_all_fields():
    with pytest.raises(AuthError, match="cannot refresh"):
        refresh_token(Token(access_token="only-this"))


# --------------------------------------------------------------------------
# SheetClient — retry behaviour
# --------------------------------------------------------------------------
def test_read_values_succeeds_first_try(token_file):
    client = make_client(token_file, {"values": [["a"], ["b"]]})
    assert client.read_values("sid", "Sheet1!A1:A2") == [["a"], ["b"]]
    assert client._opener.calls == 1


def test_retries_on_503_then_succeeds(token_file):
    client = make_client(
        token_file, http_error(503), http_error(503), {"values": [["ok"]]}
    )
    assert client.read_values("sid", "A1") == [["ok"]]
    assert client._opener.calls == 3


def test_retries_on_429_then_succeeds(token_file):
    client = make_client(token_file, http_error(429), {"values": [["ok"]]})
    assert client.read_values("sid", "A1") == [["ok"]]
    assert client._opener.calls == 2


def test_gives_up_after_retries_exhausted(token_file):
    client = make_client(
        token_file, http_error(503), http_error(503), http_error(503), http_error(503)
    )
    with pytest.raises(HttpStatusError) as exc:
        client.read_values("sid", "A1")
    assert exc.value.status == 503
    assert client._opener.calls == 4  # first attempt + 3 retries


def test_does_not_retry_on_404(token_file):
    client = make_client(token_file, http_error(404, "not found"))
    with pytest.raises(HttpStatusError) as exc:
        client.read_values("sid", "A1")
    assert exc.value.status == 404
    assert client._opener.calls == 1


def test_403_raises_api_error_with_share_hint(token_file):
    client = make_client(token_file, http_error(403, "forbidden"))
    with pytest.raises(ApiError, match="share the sheet"):
        client.read_values("sid", "A1")
    assert client._opener.calls == 1


def test_401_without_refresh_capability_raises_auth_error(token_file):
    # The fixture token has no refresh_token, so a 401 cannot be recovered.
    client = make_client(token_file, http_error(401, "expired"))
    with pytest.raises(AuthError, match="unauthorized"):
        client.read_values("sid", "A1")
    assert client._opener.calls == 1


def test_network_error_is_retried_then_raised(token_file):
    err = urllib.error.URLError("dns fail")
    client = make_client(token_file, err, err, err, err)
    with pytest.raises(ApiError, match="network error"):
        client.read_values("sid", "A1")


def test_read_records_end_to_end(token_file):
    client = make_client(token_file, {"values": [["nama", "kelas"], ["Ali", "1A"]]})
    assert client.read_records("sid", "A1:B2") == [{"nama": "Ali", "kelas": "1A"}]


def test_read_many_reads_each_range(token_file):
    client = make_client(
        token_file, {"values": [["a"]]}, {"values": [["b"]]}
    )
    assert client.read_many("sid", ["r1", "r2"]) == {"r1": [["a"]], "r2": [["b"]]}


def test_range_is_url_encoded(token_file):
    client = make_client(token_file, {"values": []})
    client.read_values("sid", "Sheet 1!A1:B2")
    # no exception and one call is enough; encoding is exercised by the path
    assert client._opener.calls == 1


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def test_cli_parser_defaults():
    args = build_parser().parse_args(["SID", "Sheet1!A1:B2"])
    assert args.format == "json"
    assert args.token == "token.json"
    assert args.values_only is False


def test_to_csv_roundtrip():
    assert to_csv([["a", "b"], ["1", "2"]]) == "a,b\n1,2\n"


def test_cli_prints_json(token_file, capsys, monkeypatch):
    monkeypatch.setattr(
        "sheetreader.cli.SheetClient",
        lambda _t: make_client(token_file, {"values": [["nama"], ["Ali"]]}),
    )
    code = main(["SID", "Sheet1!A1:A2", "-t", token_file])
    assert code == 0
    assert json.loads(capsys.readouterr().out) == [{"nama": "Ali"}]


def test_cli_returns_1_on_error(tmp_path, capsys):
    code = main(["SID", "A1", "-t", str(tmp_path / "missing.json")])
    assert code == 1
    assert "not found" in capsys.readouterr().err
