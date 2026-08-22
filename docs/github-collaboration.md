# Collaborating on Bill with GitHub

Git records the code and its history. GitHub adds shared planning and review:
Issues describe work, pull requests propose changes, checks test them, and
reviews improve them before they reach `main`.

## Issues: describe a problem before solving it

Open an Issue when work should be discussed, prioritized, assigned, or tracked
separately from the code change. Good examples are a reproducible bug, a feature
whose outcome needs agreement, or a follow-up discovered during review.

A useful Issue contains:

- a concise, searchable title describing the outcome or problem;
- the current problem and who it affects;
- expected behavior;
- enough reproduction or context to understand it;
- clear acceptance criteria that say when the work is done.

Do not put secrets, private server details, tokens, or personal data in an
Issue. Do not open one for a quick question better answered in chat or a
Discussion, a security report that needs private disclosure, duplicate work, or
a tiny fix already fully explained by its PR.

### Organizing and linking Issues

- **Labels** classify work, such as `bug`, `enhancement`, or documentation. Use
  a small number that convey information.
- A **milestone** groups Issues and PRs toward a time or release goal. It is not
  a promise that every item will ship.
- An **assignee** is the person currently responsible for moving the Issue
  forward, not everyone interested in it.
- Links between Issues and PRs preserve the reason for a change. Put
  `Closes #123` in the PR description when merging that PR should automatically
  close Issue 123. Use `Related to #123` when the PR is relevant but does not
  complete the Issue.

Triage means checking new Issues for clarity, reproducing bugs, identifying
duplicates, applying useful labels or milestones, setting priority, and asking
for missing information. Close an Issue when it is completed, intentionally
declined, no longer relevant, or a duplicate; leave a brief reason.

GitHub Projects can provide a board or roadmap across Issues and PRs.
Discussions are better for open-ended questions and ideas. Bill does not need
either for every small task.

## Pull requests: propose one reviewable change

A pull request (PR) compares a **head** branch containing proposed commits with
a **base** branch that should receive them. A normal Bill PR usually has a
feature branch as its head and `main` as its base.

Avoid direct pushes to `main`. A branch and PR provide a visible diff, automated
checks, review discussion, and a safe place to revise work before accepted
history changes.

### Draft and ready PRs

Open a **draft PR** when the direction is useful to share but the work is not
ready to merge. Checks still provide early feedback and reviewers can comment,
but the draft status says that approval is premature. Mark it **ready for
review** after the scope, tests, and description are complete.

Keep a PR focused on one outcome. Unrelated changes make review harder, hide
risk, and make reverts less precise. A good description answers:

1. What changed?
2. Why is it needed?
3. How was it checked?
4. Are there risks, limitations, rollout steps, screenshots, or follow-ups?
5. Which Issue does it close or relate to?

The head branch can receive more commits after the PR opens. GitHub updates the
same PR automatically.

### Checks, review, and feedback

Checks are automated evidence, not a substitute for review. For Bill, Python
and Worker checks should pass before merge. Read failures rather than rerunning
them blindly.

A reviewer can approve, comment, or request changes. Treat a review thread as a
conversation about the code:

1. Understand the concern and ask for clarification if needed.
2. Make the change in the same head branch, or explain respectfully why another
   approach is safer.
3. Push the new commit.
4. Reply with what changed and resolve the thread only when the concern is
   addressed.

Do not hide unresolved concerns by resolving threads without replying. New
commits may make an earlier approval stale, so check whether another review is
required.

### Merging and branch cleanup

Bill can use the practical merge methods described in
[version control](version-control.md): merge commits preserve branch commits,
squash creates one commit per PR, and rebase creates linear commit history. Use
the repository's enabled method and write a useful final commit message.

After a normal PR is safely merged, delete its remote branch if it is no longer
needed, then update local `main`. Never delete an unmerged branch until its work
is preserved elsewhere. Branch cleanup is different for an active stack because
upper branches still depend on lower ones.

## Native stacked pull requests

A stack divides one dependent change into small reviewable layers. Think
bottom-to-top:

- the bottom branch starts from the trunk, normally `main`;
- each higher branch starts from the branch immediately below it;
- the bottom PR targets `main`;
- each higher PR targets the branch immediately below.

Each layer should have one branch, one worktree or coding session, and one PR.
Commit and push a lower layer before creating the branch above it so the
dependency is explicit and recoverable.

For a hypothetical Bill documentation improvement:

```text
main
  |
  +-- docs/glossary             PR 1: base main
        |
        +-- docs/git-guide      PR 2: base docs/glossary
              |
              +-- docs/exercise PR 3: base docs/git-guide
```

PR 1 introduces shared terms. PR 2 uses those terms in a guide. PR 3 adds an
exercise that depends on both. Reviewers see only each layer's incremental diff.

### A dependent chain versus a registered GitHub Stack

Branches and PR bases can form a dependent chain without extra metadata.
GitHub's native stacked PR feature explicitly registers eligible PRs as one
**Stack**. Registration adds a stack map, stack-aware review/check requirements,
cascading rebase support, and stack merge behavior.

On GitHub's website, choose **Create stack** when opening an upper PR against
the branch below, or accept GitHub's recommendation to turn an eligible chain
into a Stack. `gh stack` is an official GitHub CLI extension, not a built-in
command. Check that GitHub CLI is installed, then install the extension once:

```bash
gh extension install github/gh-stack
```

Extension installation changes the local machine but does not open or modify
Issues or PRs. With the extension available, the native flow is:

```bash
gh stack init docs/glossary
# edit, git add, and git commit the bottom layer
gh stack push
gh stack add docs/git-guide
# edit, git add, and git commit the upper layer
gh stack submit
```

`gh stack submit` pushes the branches, creates the correctly based PRs, and
links them with native Stack metadata. The feature may be in public preview, so
check the current GitHub UI and CLI documentation before relying on it for
critical work.

### When a stack helps

Use a stack when layers genuinely depend on each other but can be understood,
checked, and potentially reverted separately. Examples include a schema change
followed by an API followed by UI, or shared documentation followed by several
focused guides.

Use one normal PR when the change is small, the layers would be artificial, or
reviewing an upper layer without repeatedly revisiting the lower layer would be
harder. A stack creates coordination and rebase cost; it is not a way to avoid
making each PR coherent.

### Review, checks, and merge behavior

GitHub evaluates each registered stacked PR against the stack's trunk rules,
even though an upper PR directly targets another feature branch. Workflows for
PRs targeting `main` run for every layer. Each layer has its own review and
checks, and upper layers include the lower code when tested.

Stacks merge from the bottom upward. GitHub can merge a contiguous group as one
stack operation; selecting a higher layer also includes every unmerged layer
below it. After lower layers merge, GitHub rebases the next layer onto the trunk
so it becomes the new bottom. Review the stack map carefully before merging.

If `main` advances or a lower branch changes, the stack may stop being linear.
Use GitHub's **Rebase stack** action for a server-side cascading rebase, or:

```bash
gh stack rebase
gh stack push
```

The rebase starts at the bottom and reapplies every upper layer. It retriggers
checks. If a conflict occurs, resolve the correct layer, stage the result, and
use `gh stack rebase --continue`; use `gh stack rebase --abort` to return to the
pre-rebase state.

A rebase rewrites commit identities and updating rebased remote branches
requires force-with-lease behavior. Never casually rebase or force-push a branch
owned by another active worktree, coding session, or contributor. Coordinate
with its owner first; otherwise you can invalidate their local history, lose
commits, and disrupt every layer above it.

## Safe hands-on exercises

Use documentation-only files and disposable branches. Read each command before
running it. Commands marked **REMOTE MUTATION** change shared GitHub state.

### 1. Issue to draft PR

1. In GitHub, open a practice Issue using the repository's feature form. Give it
   an acceptance criterion such as "a practice glossary defines one harmless
   term." Creating the Issue is a **REMOTE MUTATION**.
2. Create a local branch:

   ```bash
   git switch main
   git pull --ff-only
   git switch -c practice/issue-pr
   ```

3. Add a harmless documentation file, inspect it, stage it, and commit it.
4. Push it (**REMOTE MUTATION**):

   ```bash
   git push -u origin practice/issue-pr
   ```

5. Open a draft PR (**REMOTE MUTATION**) with base `main`, include
   `Closes #<issue-number>`, and explain that it is a disposable exercise:

   ```bash
   gh pr create --draft --base main --head practice/issue-pr
   ```

6. Ask for a small review, respond to the review thread, commit and push the
   improvement (**REMOTE MUTATION**), and resolve the addressed thread.
7. Close the draft PR without merging and delete the practice branch
   (**REMOTE MUTATION**) unless a maintainer wants to preserve the exercise.
   Closing an unmerged PR means its `Closes` keyword will not close the Issue;
   close the practice Issue separately with an explanation.

### 2. Sketch a two-layer documentation stack

The safest exercise is to draw a proposed stack and stop before pushing:

```text
main <- practice/stack-terms <- practice/stack-exercise
```

To create it locally, commit one disposable documentation file on
`practice/stack-terms`, then create `practice/stack-exercise` from that branch
and commit a second file. Inspect:

```bash
git log --oneline --graph main..practice/stack-exercise
git diff main...practice/stack-terms
git diff practice/stack-terms...practice/stack-exercise
```

If the repository owner approves a remote exercise, use `gh stack init`,
`gh stack add`, and `gh stack submit` as above. `gh stack submit` is a **REMOTE
MUTATION** that pushes branches and opens PRs. Alternatively push each branch,
open the bottom PR against `main`, open the upper PR against the bottom branch,
and select **Create stack** in GitHub; each push and PR creation is a **REMOTE
MUTATION**. Do not merge practice PRs into `main`.
