"""Command line interface: ``sheetreader`` prints a sheet as JSON or CSV."""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys

from .client import SheetClient
from .errors import SheetReaderError


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sheetreader",
        description="Read a Google Sheets range and print it as JSON or CSV.",
    )
    p.add_argument("spreadsheet_id", help="the ID from the sheet URL")
    p.add_argument("range", help="A1 notation, e.g. 'Sheet1!A1:F100'")
    p.add_argument(
        "-t",
        "--token",
        default="token.json",
        help="path to a Google OAuth token file (default: token.json)",
    )
    p.add_argument(
        "-f",
        "--format",
        choices=("json", "csv"),
        default="json",
        help="output format (default: json)",
    )
    p.add_argument(
        "--header-row",
        type=int,
        default=0,
        help="0-based row used as the header when formatting as JSON",
    )
    p.add_argument(
        "--values-only",
        action="store_true",
        help="print raw rows as arrays instead of dicts",
    )
    return p


def to_csv(rows: list[list[str]]) -> str:
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows(rows)
    return buf.getvalue()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    client = SheetClient(args.token)

    try:
        if args.format == "csv":
            sys.stdout.write(to_csv(client.read_values(args.spreadsheet_id, args.range)))
        elif args.values_only:
            print(json.dumps(client.read_values(args.spreadsheet_id, args.range), indent=2))
        else:
            records = client.read_records(
                args.spreadsheet_id, args.range, header_row=args.header_row
            )
            print(json.dumps(records, indent=2, ensure_ascii=False))
    except SheetReaderError as exc:
        print(f"sheetreader: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
