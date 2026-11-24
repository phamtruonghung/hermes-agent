# Triage Labels

The skills speak in terms of five canonical triage roles. This file maps those roles to the actual
label strings used in this repo's issue tracker (`phamtruonghung/hermes-agent`, see
`docs/agents/issue-tracker.md`).

| Label in mattpocock/skills | Label in our tracker | Meaning                                  |
| -------------------------- | -------------------- | ---------------------------------------- |
| `needs-triage`             | `needs-triage`       | Maintainer needs to evaluate this issue  |
| `needs-info`               | `needs-info`         | Waiting on reporter for more information |
| `ready-for-agent`          | `ready-for-agent`    | Fully specified, ready for an AFK agent  |
| `ready-for-human`          | `ready-for-human`    | Requires human implementation            |
| `wontfix`                  | `wontfix`            | Will not be actioned                     |

When a skill mentions a role (e.g. "apply the AFK-ready triage label"), use the corresponding label
string from this table. Our strings are the canonical ones, so the two columns stay identical.

## State of the labels on this tracker

Measured at `b7db40d8` with `gh label list --repo phamtruonghung/hermes-agent`:

- `wontfix` **exists** (GitHub's default set).
- `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human` **do not exist yet**. Create one
  before applying it, or `gh issue edit --add-label` creates nothing and fails:

```bash
for l in needs-triage needs-info ready-for-agent ready-for-human; do
  gh label create "$l" --repo phamtruonghung/hermes-agent
done
```

The fork's pre-existing labels (`bug`, `enhancement`, `documentation`, `question`, `good first issue`,
`help wanted`, `invalid`, `duplicate`, `accessibility`, `wontfix`) are **categorisation**, not triage
state — they do not substitute for the five roles above. `bug_report.yml` applies `bug` on creation,
so a new bug arrives with `bug` and still needs `needs-triage`.

## Interaction with the close rubric

Closing is governed by the root `AGENTS.md` § Contribution rubric, not by a label: a triage sweeper
may only close on `implemented_on_main`, `cannot_reproduce` or `incoherent`; taste-based closes are a
maintainer's call and a doubtful item stays open. Do not treat `wontfix` as a licence to close a
reproducible bug.
