#!/usr/bin/env python3
"""Validate that a Bill release tag is safe to publish."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

TAG_PATTERN = re.compile(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")


class ValidationError(Exception):
    """A release does not satisfy Bill's publishing rules."""


def version_from_tag(tag: str) -> str:
    """Return the package version represented by a strict vMAJOR.MINOR.PATCH tag."""
    match = TAG_PATTERN.fullmatch(tag)
    if match is None:
        raise ValidationError(
            f"{tag!r} is not a strict SemVer tag; expected vMAJOR.MINOR.PATCH "
            "with no leading zeroes or prerelease/build suffix"
        )
    return ".".join(match.groups())


def read_manifest_versions(repo_root: Path) -> tuple[str, str]:
    try:
        with (repo_root / "pyproject.toml").open("rb") as file:
            python_version = tomllib.load(file)["project"]["version"]
        with (repo_root / "worker/package.json").open(encoding="utf-8") as file:
            worker_version = json.load(file)["version"]
    except (KeyError, OSError, tomllib.TOMLDecodeError, json.JSONDecodeError) as error:
        raise ValidationError(f"could not read project versions: {error}") from error

    if not isinstance(python_version, str) or not isinstance(worker_version, str):
        raise ValidationError("both manifest versions must be strings")
    return python_version, worker_version


def validate_manifest_versions(tag: str, repo_root: Path) -> str:
    version = version_from_tag(tag)
    python_version, worker_version = read_manifest_versions(repo_root)
    if python_version != worker_version:
        raise ValidationError(
            "manifest versions differ: "
            f"pyproject.toml={python_version!r}, worker/package.json={worker_version!r}"
        )
    if version != python_version:
        raise ValidationError(
            f"tag {tag!r} represents {version!r}, but both manifests use {python_version!r}"
        )
    return version


def run_git(repo_root: Path, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *arguments],
        cwd=repo_root,
        check=check,
        capture_output=True,
        text=True,
    )


def validate_git_tag(tag: str, repo_root: Path, main_ref: str) -> None:
    tag_ref = f"refs/tags/{tag}"
    try:
        object_type = run_git(repo_root, "cat-file", "-t", tag_ref).stdout.strip()
    except subprocess.CalledProcessError as error:
        raise ValidationError(f"tag {tag!r} does not exist in this checkout") from error
    if object_type != "tag":
        raise ValidationError(f"tag {tag!r} is lightweight; create an annotated tag with git tag -a")

    # Resolve both revisions before the ancestry check so a missing main ref has a clear error.
    try:
        run_git(repo_root, "rev-parse", "--verify", f"{main_ref}^{{commit}}")
        tag_commit = run_git(repo_root, "rev-parse", "--verify", f"{tag_ref}^{{commit}}").stdout.strip()
    except subprocess.CalledProcessError as error:
        raise ValidationError(f"could not resolve release tag or main ref {main_ref!r}") from error

    ancestry = run_git(
        repo_root,
        "merge-base",
        "--is-ancestor",
        tag_commit,
        main_ref,
        check=False,
    )
    if ancestry.returncode == 1:
        raise ValidationError(f"tag {tag!r} points to a commit that is not on {main_ref}")
    if ancestry.returncode != 0:
        raise ValidationError(
            f"git could not compare tag {tag!r} with {main_ref!r}: {ancestry.stderr.strip()}"
        )


def validate_release(
    tag: str,
    repo_root: Path,
    *,
    main_ref: str = "origin/main",
    check_git: bool = True,
) -> str:
    version = validate_manifest_versions(tag, repo_root)
    if check_git:
        validate_git_tag(tag, repo_root, main_ref)
    return version


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate Bill's strict release tag, synchronized versions, and Git history."
    )
    parser.add_argument("tag", help="release tag in strict vMAJOR.MINOR.PATCH form")
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="repository root (defaults to the parent of scripts/)",
    )
    parser.add_argument(
        "--main-ref",
        default="origin/main",
        help="main branch ref used for the ancestry check (default: origin/main)",
    )
    parser.add_argument(
        "--skip-git-checks",
        action="store_true",
        help="only compare tag syntax and manifest versions; never use this for publishing",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        version = validate_release(
            args.tag,
            args.repo_root.resolve(),
            main_ref=args.main_ref,
            check_git=not args.skip_git_checks,
        )
    except ValidationError as error:
        print(f"release validation failed: {error}", file=sys.stderr)
        return 1

    scope = "tag and manifests" if args.skip_git_checks else "tag, manifests, and main history"
    print(f"release validation passed for Bill {version} ({scope})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
