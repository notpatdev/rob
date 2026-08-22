# Version control with Git and GitHub

This guide explains the pieces of Git that Bill uses and a safe day-to-day
workflow. Git records the repository's history; GitHub hosts a shared copy and
adds pull requests, automated checks, reviews, and releases.

## The core ideas

- A **repository** is the project and its Git history. The hidden `.git`
  directory stores that history; do not edit it by hand.
- The **working tree** is the files currently visible in your checkout. Editing
  a file changes the working tree but does not change history.
- The **staging area** is the exact set of changes selected for the next commit.
  `git add` copies a change into this area.
- A **commit** is a named snapshot with an author, message, and parent commit.
  Commits are permanent building blocks of history, not cloud backups by
  themselves.
- A **branch** is a movable name pointing to a line of commits. Work on a short
  feature branch rather than directly on `main`.
- A **remote** is another copy of the repository. In this project, `origin`
  normally means the shared GitHub repository.
- A **tag** is a durable name for one commit. Bill uses annotated release tags
  such as `v0.2.1`; unlike a branch, a release tag should never move.

Three places matter while making a commit:

```text
working tree  --git add-->  staging area  --git commit-->  branch history
```

`git status` shows how files are distributed across those places.

## Bill's normal contribution path

Start from an up-to-date `main`:

```bash
git switch main
git pull --ff-only
git switch -c explain-one-purpose
```

Then use this loop:

1. Make one focused change.
2. Inspect it with `git status` and `git diff`.
3. Stage the intended files with `git add path/to/file`.
4. Inspect the staged patch with `git diff --staged`.
5. Commit it with a short explanation: `git commit -m "Explain one purpose"`.
6. Push the branch: `git push -u origin explain-one-purpose`.
7. Open a pull request (PR) into `main`.
8. Wait for Python and Worker checks, respond to review, and push fixes to the
   same branch.
9. Merge only after checks and review are complete.

The PR makes the proposed difference visible before it enters `main`. Checks
show whether it builds and tests successfully; review checks whether the change
is understandable, safe, and appropriate.

## Merge, squash, and rebase

These are different ways to combine branch history:

- A **merge commit** preserves the feature branch's individual commits and adds
  a commit joining it to `main`. This is useful when the branch's internal
  history tells a meaningful story.
- A **squash merge** turns the PR into one new commit on `main`. It is practical
  for small PRs with fix-up commits because `main` stays easy to read.
- A **rebase** copies commits onto a newer base so history becomes linear.
  Rebasing rewrites commit identities. It is useful for cleaning up your own
  unpublished branch, but avoid rebasing a branch that other people use.

The repository's GitHub settings determine which merge buttons are available.
Whichever method is used, the reviewed result must land on `main` before it can
be released.

## `main`, tags, and GitHub Releases

`main` is the shared record of accepted work. A release tag freezes the identity
of one commit on `main`. A GitHub Release is a web page attached to that tag,
with generated notes describing merged work.

For Bill:

```text
merged commit on main -> annotated vX.Y.Z tag -> validated GitHub Release
```

The release workflow checks the tag, both package versions, Python, and the
Worker. It creates release notes only after all checks pass. It does **not**
deploy the Worker, restart the Discord bot, or otherwise change production.

## Safe inspection commands

These commands do not rewrite history:

```bash
git status
git diff
git diff --staged
git log --oneline --decorate --graph -20
git branch --all
git remote -v
git show --stat <commit-or-tag>
git tag --list
git fetch --prune
```

`git fetch` downloads remote history without merging it into your current
branch. Prefer `git pull --ff-only` on `main`: it stops rather than inventing a
merge commit when local and remote history differ.

## What `.gitignore` does

`.gitignore` tells Git which untracked paths should normally stay untracked.
Bill ignores virtual environments, dependency folders, build output, local
editor files, and secret-bearing environment/key files.

It is not a security boundary. If a secret was already committed, adding its
filename to `.gitignore` does not erase history. Revoke or rotate the secret and
ask a maintainer for help. Before every commit, use `git diff --staged` to check
that credentials, tokens, private keys, real server IDs, and local environment
files are absent.

## Straightforward merge conflicts

A conflict means Git needs a human to choose the combined result.

1. Run `git status` to see conflicted files.
2. Open each file and find the `<<<<<<<`, `=======`, and `>>>>>>>` markers.
3. Read both sides, edit the file into the correct final form, and remove all
   markers.
4. Run the relevant checks.
5. Stage the resolved file with `git add path/to/file`.
6. Complete the operation with the command Git reports in `git status`
   (commonly `git commit`, `git merge --continue`, or `git rebase --continue`).

If the intended result is unclear, stop and ask the other contributor. Do not
choose a side merely to make the markers disappear.

## Safe recovery

First inspect; most mistakes do not require deleting work:

```bash
git status
git diff
git diff --staged
```

- Staged the wrong file? `git restore --staged path/to/file` moves it out of the
  staging area but keeps the working-tree edit.
- Need to discard one uncommitted file edit? Inspect it first, then use
  `git restore path/to/file`. This discards that file's unstaged work.
- Need to undo a shared commit? `git revert <commit>` creates a new commit that
  reverses it, preserving an honest shared history.
- On the wrong branch with uncommitted work? Stop and ask before moving it if
  you are uncertain. A small commit on a temporary branch is safer than
  destructive cleanup.

Avoid `git reset --hard`: it can permanently discard uncommitted work. Never
force-push a shared branch, delete a branch whose work is not safely merged, or
commit secrets. These shortcuts can destroy another person's work or expose
credentials.

## Exercises on disposable branches

Use branches that contain no valuable work:

1. **Stage and unstage:** create `practice.txt`, inspect it, stage it, inspect
   the staged diff, then run `git restore --staged practice.txt`. Delete the
   untracked practice file afterward.
2. **Make two commits:** on `practice/two-commits`, add two harmless lines in
   separate commits. Compare `git log --oneline main..HEAD` with
   `git diff main...HEAD`.
3. **Resolve a toy conflict:** create two disposable branches that change the
   same line in `practice.txt`, merge one into the other, and resolve the
   markers. Do not use production code for the exercise.
4. **Practice revert:** commit a harmless practice file, run
   `git revert <commit>`, and inspect how both the original and reversal remain
   in `git log`.

Delete a practice branch only after switching away from it and confirming that
it contains nothing you need.
