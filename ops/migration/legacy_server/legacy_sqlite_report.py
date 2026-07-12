from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ops.migration.inspect_sqlite import inspect_sqlite
from ops.migration.legacy_server.find_sqlite_candidates import (
    DEFAULT_ROOTS,
    choose_best_candidate,
    discover_candidates,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Find and inspect the most likely legacy Rob SQLite DB."
    )
    parser.add_argument(
        "--sqlite",
        default="",
        help="Optional explicit SQLite path. If omitted, the best candidate is chosen.",
    )
    parser.add_argument(
        "--roots",
        nargs="*",
        default=list(DEFAULT_ROOTS),
        help="Directories to scan when --sqlite is not provided.",
    )
    parser.add_argument(
        "--report-json",
        default="",
        help="Optional JSON output path.",
    )
    return parser.parse_args()


def build_report(*, sqlite_path: Path, candidates: list[Any]) -> dict[str, Any]:
    inspection = inspect_sqlite(sqlite_path)
    return {
        "selected_sqlite": str(sqlite_path),
        "inspection": inspection,
        "candidates": [asdict(candidate) for candidate in candidates],
    }


def main() -> None:
    args = parse_args()
    roots = [Path(root) for root in args.roots]
    candidates = discover_candidates(roots=roots) if not args.sqlite else []

    sqlite_path = Path(args.sqlite) if args.sqlite else None
    if sqlite_path is None:
        best = choose_best_candidate(roots=roots)
        if best is None:
            raise SystemExit("No SQLite candidates were found.")
        sqlite_path = Path(best.path)

    if not sqlite_path.exists():
        raise SystemExit(f"SQLite database not found: {sqlite_path}")

    report = build_report(sqlite_path=sqlite_path, candidates=candidates)
    inspection = report["inspection"]
    print("Legacy SQLite report")
    print(f"- selected_sqlite: {report['selected_sqlite']}")
    print(f"- candidate_count: {len(report['candidates'])}")
    for table_name, count in inspection["table_counts"].items():
        print(f"- {table_name}: {count}")
    print(f"- event_sends_total_usd: {inspection['event_sends_total_usd']:.2f}")

    if args.report_json:
        report_path = Path(args.report_json)
        report_path.write_text(
            json.dumps(report, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        print(f"- report_json_written: {report_path}")


if __name__ == "__main__":
    main()
