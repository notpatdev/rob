# Contributing to Bill

Bill uses small reviewed changes rather than direct pushes to `main`.

1. Open or choose a clear Issue when the work needs tracking or agreement.
2. Start one focused branch or coding session from current `main`.
3. Make the smallest coherent change; never include secrets or production IDs.
4. Inspect and commit the intended patch, then push the feature branch.
5. Open a focused PR into `main`, link its Issue, and describe what changed and
   how it was checked. Use a draft while work is incomplete.
6. Run and fix the Python and Worker checks documented in the
   [README](README.md).
7. Address review conversations in the same branch and resolve threads only
   after replying.
8. Merge with an enabled repository strategy after checks and review pass.
   Delete the branch only when its work is safely merged and no stack depends
   on it.

Use stacked PRs only for genuinely dependent, separately reviewable layers.
Coordinate before rebasing or force-updating any shared or session-owned branch.

Read [version control](docs/version-control.md), [GitHub collaboration](docs/github-collaboration.md),
and [releases](docs/releases.md) before changing shared history or publishing a
version. Creating a GitHub Release never deploys Bill; production rollout
remains a separate manual process.
