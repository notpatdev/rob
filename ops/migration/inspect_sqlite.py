from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any


TABLES_OF_INTEREST = (
    "bot_config",
    "event_dommes",
    "event_messages",
    "event_sends",
    "event_state",
    "event_subs",
    "rob_blacklist",
    "send_requests",
    "throne_creators",
    "throne_wishlist_items",
)


def _fetch_count(connection: sqlite3.Connection, table: str) -> int:
    try:
        row = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(row[0] if row else 0)


def _fetch_send_total_usd(connection: sqlite3.Connection) -> float:
    try:
        row = connection.execute("SELECT COALESCE(SUM(amount_usd), 0) FROM event_sends").fetchone()
    except sqlite3.OperationalError:
        return 0.0
    return float(row[0] if row else 0.0)


def inspect_sqlite(path: Path) -> dict[str, Any]:
    with sqlite3.connect(path) as sqlite:
        sqlite.row_factory = sqlite3.Row
        rows: dict[str, int] = {table: _fetch_count(sqlite, table) for table in TABLES_OF_INTEREST}
        send_total_usd = _fetch_send_total_usd(sqlite)
    return {
        "sqlite_path": str(path),
        "table_counts": rows,
        "event_sends_total_usd": round(send_total_usd, 2),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect legacy Rob SQLite DB counts.")
    parser.add_argument("--sqlite", required=True, help="Path to legacy SQLite database file.")
    parser.add_argument("--report-json", default="", help="Optional path to write JSON report.")
    args = parser.parse_args()

    report = inspect_sqlite(Path(args.sqlite))
    print("SQLite inspection summary")
    print(f"- sqlite_path: {report['sqlite_path']}")
    for table in TABLES_OF_INTEREST:
        print(f"- {table}: {report['table_counts'][table]}")
    print(f"- event_sends_total_usd: {report['event_sends_total_usd']:.2f}")

    if args.report_json:
        output_path = Path(args.report_json)
        output_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
        print(f"- report_json_written: {output_path}")


if __name__ == "__main__":
    main()

