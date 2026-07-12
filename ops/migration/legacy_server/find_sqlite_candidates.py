from __future__ import annotations

import argparse
import json
import os
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from ops.migration.inspect_sqlite import TABLES_OF_INTEREST


DEFAULT_ROOTS = (
    "/opt",
    "/srv",
    "/var",
    "/home/ec2-user",
    "/home/ubuntu",
)
KNOWN_FILENAMES = {
    "rob_the_bot.sqlite3",
    "db.sqlite3",
    "rob.sqlite3",
    "rob.db",
}
SQLITE_SUFFIXES = (".sqlite", ".sqlite3", ".db")


@dataclass(frozen=True)
class SQLiteCandidate:
    path: str
    size_bytes: int
    modified_at: str
    matched_tables: tuple[str, ...]
    table_match_count: int
    preferred_filename: bool
    score: tuple[int, int, int]


def _safe_tables(path: Path) -> tuple[str, ...]:
    try:
        with sqlite3.connect(path) as connection:
            rows = connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
    except sqlite3.Error:
        return ()
    table_names = {str(row[0]) for row in rows}
    matched = sorted(table for table in TABLES_OF_INTEREST if table in table_names)
    return tuple(matched)


def _is_candidate(path: Path) -> bool:
    if not path.is_file():
        return False
    name = path.name.lower()
    if any(name.endswith(suffix) for suffix in SQLITE_SUFFIXES):
        return True
    return path.name in KNOWN_FILENAMES


def _walk_roots(roots: Iterable[Path]) -> list[Path]:
    candidates: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [
                name
                for name in dirnames
                if name not in {".git", ".venv", "node_modules", "__pycache__"}
            ]
            base = Path(dirpath)
            for filename in filenames:
                path = base / filename
                if _is_candidate(path):
                    candidates.append(path)
    return candidates


def discover_candidates(*, roots: Iterable[Path]) -> list[SQLiteCandidate]:
    discovered: list[SQLiteCandidate] = []
    for path in _walk_roots(roots):
        stat = path.stat()
        matched_tables = _safe_tables(path)
        preferred_filename = path.name in KNOWN_FILENAMES
        candidate = SQLiteCandidate(
            path=str(path),
            size_bytes=stat.st_size,
            modified_at=datetime.fromtimestamp(
                stat.st_mtime, tz=timezone.utc
            ).isoformat(),
            matched_tables=matched_tables,
            table_match_count=len(matched_tables),
            preferred_filename=preferred_filename,
            score=(len(matched_tables), 1 if preferred_filename else 0, int(stat.st_mtime)),
        )
        discovered.append(candidate)
    return sorted(
        discovered,
        key=lambda item: item.score,
        reverse=True,
    )


def choose_best_candidate(*, roots: Iterable[Path]) -> SQLiteCandidate | None:
    candidates = discover_candidates(roots=roots)
    return candidates[0] if candidates else None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scan legacy server paths for likely Rob SQLite databases."
    )
    parser.add_argument(
        "--roots",
        nargs="*",
        default=list(DEFAULT_ROOTS),
        help="Directories to scan for SQLite candidates.",
    )
    parser.add_argument(
        "--report-json",
        default="",
        help="Optional JSON output path.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    roots = [Path(root) for root in args.roots]
    candidates = discover_candidates(roots=roots)

    print("Legacy SQLite candidates")
    print(f"- scanned_roots: {', '.join(str(root) for root in roots)}")
    if not candidates:
        print("- candidates_found: 0")
        return

    print(f"- candidates_found: {len(candidates)}")
    for index, candidate in enumerate(candidates[:20], start=1):
        matched = ", ".join(candidate.matched_tables) if candidate.matched_tables else "(no Rob tables matched)"
        print(
            f"- [{index}] {candidate.path} | size={candidate.size_bytes} | "
            f"mtime={candidate.modified_at} | matched_tables={candidate.table_match_count} | {matched}"
        )

    if args.report_json:
        report_path = Path(args.report_json)
        report_path.write_text(
            json.dumps(
                {
                    "roots": [str(root) for root in roots],
                    "candidates": [asdict(candidate) for candidate in candidates],
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        print(f"- report_json_written: {report_path}")


if __name__ == "__main__":
    main()
