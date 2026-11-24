# Issue tracker: GitHub

Issues and specs live as GitHub issues on **upstream `NousResearch/hermes-agent`**. This clone's
`origin`, `phamtruonghung/hermes-agent`, is a **fork with issues disabled**
(`gh api repos/phamtruonghung/hermes-agent --jq .has_issues` → `false`), so every `gh issue` write
against it fails with "this repository has disabled issues". `gh label list` still answers there
(labels are repo metadata), which is not evidence the write path works. The fork's own surface is
**pull requests**.

Pin the target explicitly on every `gh issue` command:

```bash
gh issue create --repo NousResearch/hermes-agent --title "..." --body "..."
gh pr create --repo phamtruonghung/hermes-agent ...   # work on this fork lands as a PR
```

## Which tracker for what

- **Work on this clone** (a slice we are implementing here, local defects, our own follow-ups) →
  a PR on the fork (`phamtruonghung/hermes-agent`); the PR body carries the spec.
- **A defect in the product itself** that belongs to the maintainers → an upstream issue
  (`NousResearch/hermes-agent`), per `CONTRIBUTING.md`. The fork's issue-template contact links and
  `SECURITY.md` advisories also point upstream.
- **A §3.1 security finding never goes to any public issue, PR, commit or comment** — private
  advisory (GitHub Security Advisories or security@nousresearch.com). `SECURITY.md` is the scope
  authority; §3.2 hardening is ordinary public work.

## Conventions

- **Create an issue**: `gh issue create --repo NousResearch/hermes-agent --title "..." --body "..."`.
  Use a heredoc for multi-line bodies.
- **Read an issue**: `gh issue view <number> --repo NousResearch/hermes-agent --json number,title,body,labels,comments`.
- **List issues**: `gh issue list --repo NousResearch/hermes-agent --state open --json number,title,body,labels,comments --jq '[.[] | {number, title, body, labels: [.labels[].name], comments: [.comments[].body]}]'`
  with appropriate `--label` and `--state` filters.
- **Make an issue a sub-issue of a parent**: `gh issue create --repo NousResearch/hermes-agent --parent <parent> ...`, or
  `gh issue edit <parent> --repo NousResearch/hermes-agent --add-sub-issue <child>` (`gh` 2.94+). Older `gh`:
  `gh api --method POST repos/NousResearch/hermes-agent/issues/<parent>/sub_issues -F sub_issue_id=<child-db-id>`
  (database id). Without sub-issues, put `Part of #<parent>` at the top of the child body.
- **Blocking**: GitHub's **native issue dependencies**. Add an edge with
  `gh issue create --repo NousResearch/hermes-agent --blocked-by <n>,<n> ...` or `gh issue edit <child> --repo NousResearch/hermes-agent --add-blocked-by <blocker>`
  (`gh` 2.94+). Older `gh`:
  `gh api --method POST repos/NousResearch/hermes-agent/issues/<child>/dependencies/blocked_by -F issue_id=<blocker-db-id>`,
  where `<blocker-db-id>` is the blocker's numeric **database id**
  (`gh api repos/NousResearch/hermes-agent/issues/<n> --jq .id`, _not_ the `#number` or `node_id`). Where
  dependencies aren't available, fall back to a `Blocked by: #<n>, #<n>` line at the top of the child
  body. A ticket is unblocked when every blocker is closed.
- **Count open blockers**: `gh api repos/NousResearch/hermes-agent/issues/<n> --jq .issue_dependencies_summary.blocked_by`
  (open blockers only; 0 means startable). `gh issue view <n> --repo NousResearch/hermes-agent --json blockedBy` also lists closed
  blockers, so filter it: `--jq '[.blockedBy.nodes[] | select(.state == "OPEN")] | length'`.
- **Comment on an issue**: `gh issue comment <number> --repo NousResearch/hermes-agent --body "..."`.
- **Apply / remove labels**: `gh issue edit <number> --repo NousResearch/hermes-agent --add-label "..."` / `--remove-label "..."`.
  Vocabulary: `docs/agents/triage-labels.md`.
- **Close**: post the explanation with `gh issue comment <number> --repo NousResearch/hermes-agent --body "..."`, then
  `gh issue close <number> --repo NousResearch/hermes-agent`. On an already-closed issue, `gh issue close --comment` drops the comment
  and still exits 0.

## Search before filing

The issue tracker lags the code: many requested features are already implemented in-tree, so also
search the source for the capability before proposing it.

```bash
gh search issues --repo NousResearch/hermes-agent "<terms>"     # upstream issues, open AND closed
gh search prs --repo NousResearch/hermes-agent "<terms>"       # upstream PRs, open AND merged
gh search prs --repo phamtruonghung/hermes-agent "<terms>"   # this fork's PRs
git log -p -S "<symbol>"                                        # the intent behind existing code
```

`CONTRIBUTING.md` § Before You Start: Search First carries the human-contributor version of this list.

## Issue templates in this repo

`.github/ISSUE_TEMPLATE/`: `bug_report.yml` (auto-applies the `bug` label, requires a description,
repro steps and a `hermes debug share` report), `feature_request.yml`, `setup_help.yml`. Blank issues
are enabled (`config.yml: blank_issues_enabled: true`). `gh issue create` does not fill a form, so
mirror the template's required fields in the body yourself.

## Closing: the rubric, not the mood

The triage sweeper in the root `AGENTS.md` § Contribution rubric may only close on
`implemented_on_main`, `cannot_reproduce` or `incoherent`. Taste-based closes are a maintainer's
call, and when in doubt an issue or PR stays open. A fix that cannot point to the line where the bug
manifests has an unverified premise — say so instead of closing.

## Pull requests as a triage surface

**PRs as a request surface: no.** _(Set to `yes` if this repo treats external PRs as feature
requests; `/triage` reads this flag.)_

When set to `yes`, PRs run through the same labels and states as issues, using the `gh pr`
equivalents:

- **Read a PR**: `gh pr view <number>`, then `gh pr view <number> --comments` (off a TTY, `--comments`
  prints only the comments, not the body), and `gh pr diff <number>` for the diff.
- **List external PRs for triage**: `gh api --paginate 'repos/phamtruonghung/hermes-agent/pulls?state=open' --jq '.[] | select(.author_association | IN("OWNER","MEMBER","COLLABORATOR") | not) | {number, title, author: .user.login, author_association, labels: [.labels[].name]}'`.
- **Comment / label / close**: `gh pr comment`, `gh pr edit --add-label`/`--remove-label`,
  `gh pr close`.

GitHub shares one number space across issues and PRs, so a bare `#42` may be either: resolve with
`gh pr view 42` and fall back to `gh issue view 42 --repo NousResearch/hermes-agent` (a fork has no issues of its own).

## When a skill says "publish to the issue tracker"

Create a GitHub issue on upstream `NousResearch/hermes-agent` (fork issues are disabled); work implemented on this
fork goes up as a PR on `phamtruonghung/hermes-agent` — see **Which tracker for what**.

## When a skill says "fetch the relevant ticket"

Read it as in **Read an issue** above (`--repo NousResearch/hermes-agent`), or `gh pr view <n>` when the ticket is a
fork PR.

## Wayfinding operations

Used by `/wayfinder`. The **map** is a single issue with **child** issues as tickets.

- **Map**: a single issue labelled `wayfinder:map`, holding the Notes / Decisions-so-far / Fog body.
  `gh issue create --repo NousResearch/hermes-agent --label wayfinder:map` (create the label first with
  `gh label create wayfinder:map --repo NousResearch/hermes-agent` if absent).
- **Child ticket**: an issue linked to the map as a GitHub sub-issue (see **Make an issue a
  sub-issue of a parent**). Where sub-issues aren't enabled, add the child to a task list in the map
  body and put `Part of #<map>` at the top of the child body. Labels: `wayfinder:<type>`
  (`research`/`prototype`/`grilling`/`task`). Once claimed, the ticket is assigned to the driving dev.
- **Blocking**: see **Blocking** under Conventions.
- **Frontier query**: list the map's open children (`gh issue list --repo NousResearch/hermes-agent --state open`, scoped to the map's
  sub-issues / task list), drop any with an open blocker (see **Count open blockers**, or an open
  issue in the `Blocked by` line) or an assignee; first in map order wins.
- **Claim**: `gh issue edit <n> --repo NousResearch/hermes-agent --add-assignee @me`, the session's first write.
- **Resolve**: `gh issue comment <n> --repo NousResearch/hermes-agent --body "<answer>"`, then
  `gh issue close <n> --repo NousResearch/hermes-agent`, then append a
  context pointer to the map's Decisions-so-far.
