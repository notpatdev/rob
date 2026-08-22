from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scripts.validate_release import ValidationError, validate_release, version_from_tag


def run_git(repo: Path, *arguments: str) -> None:
    subprocess.run(["git", *arguments], cwd=repo, check=True, capture_output=True, text=True)


def write_manifests(repo: Path, python_version: str, worker_version: str) -> None:
    (repo / "worker").mkdir(exist_ok=True)
    (repo / "pyproject.toml").write_text(
        f'[project]\nname = "bill-discord-bot"\nversion = "{python_version}"\n',
        encoding="utf-8",
    )
    (repo / "worker/package.json").write_text(
        json.dumps({"name": "bill-worker", "version": worker_version}),
        encoding="utf-8",
    )


@pytest.fixture
def release_repo(tmp_path: Path) -> Path:
    run_git(tmp_path, "init", "-b", "main")
    run_git(tmp_path, "config", "user.name", "Release Test")
    run_git(tmp_path, "config", "user.email", "release-test@example.invalid")
    write_manifests(tmp_path, "0.1.0", "0.1.0")
    run_git(tmp_path, "add", "pyproject.toml", "worker/package.json")
    run_git(tmp_path, "commit", "-m", "Prepare release")
    run_git(tmp_path, "tag", "-a", "v0.1.0", "-m", "Bill v0.1.0")
    return tmp_path


def test_accepts_annotated_matching_tag_on_main(release_repo: Path) -> None:
    assert validate_release("v0.1.0", release_repo, main_ref="main") == "0.1.0"


@pytest.mark.parametrize(
    "tag",
    [
        "0.1.0",
        "v0.1",
        "v01.1.0",
        "v0.1.0-rc.1",
        "v0.1.0+build",
        "v0.1.0extra",
        "v\u0661.2.3",
    ],
)
def test_rejects_non_strict_tags(tag: str) -> None:
    with pytest.raises(ValidationError, match="strict SemVer"):
        version_from_tag(tag)


@pytest.mark.parametrize(
    ("python_version", "worker_version", "message"),
    [("0.1.1", "0.1.0", "manifest versions differ"), ("0.1.1", "0.1.1", "tag .* represents")],
)
def test_rejects_manifest_version_mismatches(
    release_repo: Path,
    python_version: str,
    worker_version: str,
    message: str,
) -> None:
    write_manifests(release_repo, python_version, worker_version)
    with pytest.raises(ValidationError, match=message):
        validate_release("v0.1.0", release_repo, main_ref="main", check_git=False)


def test_rejects_lightweight_tag(release_repo: Path) -> None:
    run_git(release_repo, "tag", "v0.1.1")
    write_manifests(release_repo, "0.1.1", "0.1.1")
    with pytest.raises(ValidationError, match="lightweight"):
        validate_release("v0.1.1", release_repo, main_ref="main")


def test_rejects_tag_not_on_main(release_repo: Path) -> None:
    run_git(release_repo, "switch", "-c", "not-main")
    (release_repo / "branch-only.txt").write_text("branch-only\n", encoding="utf-8")
    run_git(release_repo, "add", "branch-only.txt")
    run_git(release_repo, "commit", "-m", "Branch-only commit")
    run_git(release_repo, "tag", "-a", "v0.1.1", "-m", "Bill v0.1.1")
    write_manifests(release_repo, "0.1.1", "0.1.1")

    with pytest.raises(ValidationError, match="not on main"):
        validate_release("v0.1.1", release_repo, main_ref="main")
