from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

from ops.migration.legacy_server.find_sqlite_candidates import discover_candidates


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_legacy_migration_helper_files_exist():
    assert (REPO_ROOT / "ops/migration/legacy_server/find_sqlite_candidates.py").exists()
    assert (REPO_ROOT / "ops/migration/legacy_server/legacy_sqlite_report.py").exists()
    assert (REPO_ROOT / "ops/migration/legacy_server/legacy_to_pg_dry_run.sh").exists()
    assert (REPO_ROOT / "ops/migration/legacy_server/legacy_to_pg_apply.sh").exists()


def test_find_sqlite_candidates_prefers_db_with_rob_tables(tmp_path: Path):
    db_path = tmp_path / "rob_the_bot.sqlite3"
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE event_dommes (id INTEGER PRIMARY KEY)")
        connection.execute("CREATE TABLE event_sends (id INTEGER PRIMARY KEY, amount_usd REAL)")
        connection.commit()

    other_path = tmp_path / "random.sqlite3"
    with sqlite3.connect(other_path) as connection:
        connection.execute("CREATE TABLE something_else (id INTEGER PRIMARY KEY)")
        connection.commit()

    candidates = discover_candidates(roots=[tmp_path])

    assert candidates
    assert candidates[0].path == str(db_path)
    assert "event_dommes" in candidates[0].matched_tables


def test_legacy_sqlite_report_outputs_selected_path(tmp_path: Path):
    db_path = tmp_path / "rob_the_bot.sqlite3"
    report_path = tmp_path / "report.json"
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE event_dommes (id INTEGER PRIMARY KEY)")
        connection.execute("CREATE TABLE event_sends (id INTEGER PRIMARY KEY, amount_usd REAL)")
        connection.commit()

    result = subprocess.run(
        [
            "python3",
            "-m",
            "ops.migration.legacy_server.legacy_sqlite_report",
            "--sqlite",
            str(db_path),
            "--report-json",
            str(report_path),
        ],
        check=True,
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )

    assert "selected_sqlite" in result.stdout
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["selected_sqlite"] == str(db_path)


def test_legacy_shell_wrappers_reference_v2_importer():
    dry_run = (REPO_ROOT / "ops/migration/legacy_server/legacy_to_pg_dry_run.sh").read_text(encoding="utf-8")
    apply = (REPO_ROOT / "ops/migration/legacy_server/legacy_to_pg_apply.sh").read_text(encoding="utf-8")

    assert "--dry-run" in dry_run
    assert "import_sqlite_to_postgres" in dry_run
    assert "--exclude-domme-handle" in dry_run
    assert "--default-guild-id is omitted" in dry_run
    assert "--no-dry-run" in apply
    assert "--confirm-apply yes" in apply
    assert "--exclude-domme-handle" in apply
    assert "--default-guild-id is omitted" in apply
