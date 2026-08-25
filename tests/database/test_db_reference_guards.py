from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def _runtime_python_files() -> list[Path]:
    files: list[Path] = []
    for path in (REPO_ROOT / "rob").rglob("*.py"):
        relative = path.relative_to(REPO_ROOT).as_posix()
        if relative.startswith("rob/database/migrations/"):
            continue
        files.append(path)
    return files


def test_runtime_code_does_not_reference_legacy_leaderboard_messages_table():
    offenders: list[str] = []
    for file_path in _runtime_python_files():
        text = file_path.read_text(encoding="utf-8")
        if "leaderboard_messages" in text:
            offenders.append(file_path.relative_to(REPO_ROOT).as_posix())
    assert offenders == []


def test_runtime_code_does_not_reference_legacy_throne_wishlist_items_table():
    offenders: list[str] = []
    for file_path in _runtime_python_files():
        text = file_path.read_text(encoding="utf-8")
        if "throne_wishlist_items" in text:
            offenders.append(file_path.relative_to(REPO_ROOT).as_posix())
    assert offenders == []
