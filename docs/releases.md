# Releasing Bill

Bill follows [Semantic Versioning](https://semver.org/) using strict
`MAJOR.MINOR.PATCH` versions. Because Bill is still below `1.0.0`, minor releases
may contain intentional breaking changes and patch releases should remain
backward-compatible fixes.

Examples from `0.1.0`:

- `0.1.1` is a patch: a compatible bug or documentation fix.
- `0.2.0` is a minor: new functionality or an intentional compatibility change
  while the product is pre-1.0.
- `1.0.0` is a major: Bill's first declared stable public contract. After 1.0,
  incompatible changes increment the major version.

GitHub's generated release notes are the authoritative changelog. The release
workflow does not deploy anything: Worker deployment and Discord bot rollout
remain separate, manual operations.

## Release procedure

Replace `0.1.1` below with the intended version. Use only a final
`MAJOR.MINOR.PATCH` version; prerelease and build suffixes are not accepted by
the initial workflow.

### 1. Prepare and merge a version PR

Create a focused branch from current `main`:

```bash
git switch main
git pull --ff-only
git switch -c release/0.1.1
```

Update `[project].version` in `pyproject.toml` and `version` in
`worker/package.json` to exactly `0.1.1`. Keep `worker/package-lock.json`
synchronized by using npm's version command:

```bash
npm --prefix worker version 0.1.1 --no-git-tag-version
```

That command updates the Worker package manifest and lock file without making a
Git tag. Update `pyproject.toml` separately, then check the planned tag against
the manifests:

```bash
python scripts/validate_release.py v0.1.1 --skip-git-checks
git diff -- pyproject.toml worker/package.json worker/package-lock.json
```

Commit the version changes, push the branch, open a PR, and wait for all checks
and review. Merge the PR into `main`. Do not tag the feature branch or the
unmerged PR commit.

### 2. Create and inspect the annotated tag on `main`

Return to `main`, update it without creating a local merge, and fetch tags:

```bash
git switch main
git pull --ff-only
git fetch origin --tags
```

Confirm both versions and create an **annotated** tag:

```bash
python scripts/validate_release.py v0.1.1 --skip-git-checks
git tag -a v0.1.1 -m "Bill v0.1.1"
git show --no-patch --decorate v0.1.1
python scripts/validate_release.py v0.1.1 --main-ref origin/main
```

The final validator command proves that the tag is strict SemVer, annotated,
matches both manifests, and points to a commit contained in `origin/main`.

### 3. Push only the tag

```bash
git push origin v0.1.1
```

Do not use `git push --tags`; it could publish unrelated local tags. Pushing the
single tag starts `.github/workflows/release.yml`.

Watch the **Release** workflow in GitHub Actions. It installs dependencies and
runs the same Python compile, Ruff, pytest, Worker typecheck, and Vitest checks
as CI. Only after they all pass does it create **Bill v0.1.1** with GitHub
generated notes. Rerunning the workflow reports an existing release and leaves
its content unchanged.

Inspect the GitHub Release page and its generated notes. The release records
source history only. When production should receive that version, make a
separate decision and follow [deployment](deployment.md) for the Worker and
Discord bot. Creating the release never runs `wrangler deploy`, connects to the
production host, or restarts the bot.

## Rollback and correction

Tags and GitHub Releases are historical records. Do not move or reuse a
published version tag, and do not treat deleting a release as a production
rollback.

If source code needs correction, revert the bad commit in a reviewed PR, choose
a new patch version, and run the release procedure again. If production needs
rollback, separately decide which known-good commit or release to deploy and
perform the appropriate manual Worker/bot rollout. A source revert, a new
release, and a production rollback are related decisions but not the same
operation.

If an incorrect tag has not produced a release and nobody else relies on it,
ask a maintainer before deleting or replacing it. Never force-update a published
release tag.

## Troubleshooting

### The tag format is rejected

Use exactly `vMAJOR.MINOR.PATCH`, such as `v0.1.1`. `0.1.1`, `v01.1.0`,
`v0.1`, `v0.1.1-rc.1`, and `v0.1.1+build` are intentionally rejected.

### The tag is lightweight

Delete only the unpushed local tag, then recreate it with an annotation:

```bash
git tag -d v0.1.1
git tag -a v0.1.1 -m "Bill v0.1.1"
```

Do not replace a tag that has already been pushed without maintainer help.

### The versions do not match

The version after `v` must exactly match both `pyproject.toml` and
`worker/package.json`. Correct the manifests and lock file in a PR, merge it,
update local `main`, and tag the merged version commit. Do not edit manifests
directly on `main`.

### The tagged commit is not on `main`

The PR may be unmerged, local `main` may be stale, or the tag may have been
created while another branch was checked out. Inspect:

```bash
git status
git branch --show-current
git fetch origin main
git show --no-patch --decorate v0.1.1
git branch --remote --contains v0.1.1
```

If the tag is still local, delete it and recreate it on updated `main`. If it
was pushed, stop and ask a maintainer rather than rewriting shared history.

### Python or Worker checks fail

No release is published. Fix the failure through a new PR, merge it, increment
to a new version if the tag was already shared, and create a new tag. A failed
workflow must never be bypassed by creating a release manually.

### The release already exists

This is expected on a workflow rerun. The publish job reports the existing URL
and does not overwrite generated or maintainer-edited notes.
