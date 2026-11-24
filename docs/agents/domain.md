# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the
codebase.

## Layout in this repo: single-context

One product domain — the Hermes agent runtime — with one deployable codebase. There is no
`CONTEXT-MAP.md` and no `packages/*` tree, so the single-context layout applies:

```
/
├── CONTEXT.md                  ← lazily created by /domain-modeling when terms get resolved
├── docs/adr/                   ← system-wide decisions, lazily created
├── AGENTS.md
└── website/docs/developer-guide/   ← existing long-form area docs
```

The root `pnpm-workspace.yaml` (`apps/*`, `ui-tui`, `ui-tui/packages/*`, `web`, `tests-js`) spans the
TypeScript surfaces of the *same* product, not separate bounded contexts — it is a workspace file,
not a context map.

If `CONTEXT.md` or `docs/adr/` don't exist, **proceed silently**. Don't flag their absence; don't
suggest creating them upfront. `/domain-modeling` (reached via `/grill-with-docs` and
`/improve-codebase-architecture`) creates them lazily when terms or decisions actually get resolved.

## Before exploring, read these

- **`CONTEXT.md`** at the repo root, when it exists.
- **`docs/adr/`** — read the ADRs touching the area you're about to work in, when they exist.
- **The root `AGENTS.md`** and the **area `AGENTS.md`** for the directory you are entering (14 area
  files: `agent/`, `gateway/`, `tools/`, `plugins/`, `hermes_cli/`, `hermes_platform/`, `pm/`,
  `skills/`, `cron/`, `tests/`, `web/`, `tui_gateway/`, `apps/desktop/`, `apps/desktop/src/`). The
  root file's Routing Table maps an area to the file to read. **These area files are the closest
  thing this repo has to context docs today — read them before the source.**
- **`website/docs/developer-guide/architecture.md`** for the system overview, data flow and major
  subsystems, **`…/codebase-ownership.md`** for the authoritative subsystem → source-directory →
  docs-entry-point table, and **`…/contributing.md`** for the contribution rubric in long form.

## The subsystems (modules of one context, not separate contexts)

They share the session/message model, the config surface and the prompt-cache invariant, which is why
this is one bounded context rather than many. Names as used in `codebase-ownership.md`:

| Subsystem | Source directories |
|---|---|
| Agent core (turn loop, transports, compression) | `agent/`, `run_agent.py` |
| Prompt assembly | `agent/prompt_builder.py`, `agent/system_prompt.py` |
| Model providers & transports | `agent/transports/`, `plugins/model-providers/`, `hermes_cli/models.py` |
| Built-in tools | `tools/` |
| Messaging gateway | `gateway/`, `plugins/platforms/` |
| CLI | `hermes_cli/` |
| Plugins system | `plugins/` |
| Skills (bundled & optional) | `skills/`, `optional-skills/` |
| Cron / scheduled jobs, kanban | `cron/` |
| Session storage | `hermes_state.py`, `hermes_state_*.py` |
| Browser stack | `tools/browser_*.py` |
| ACP (IDE integration) | `acp_adapter/` |
| Desktop app | `apps/desktop/` |
| TUI | `ui-tui/`, `tui_gateway/` |
| Docs site | `website/` |
| PM / packaging | `pm/`, `pyproject.toml` |
| Tests | `tests/`, `tests-js/` |

## Use the glossary's vocabulary

When your output names a domain concept (in an issue title, a refactor proposal, a hypothesis, a test
name), use the term as `CONTEXT.md` defines it, once it exists — and until then the subsystem names
above and the two invariants in the root `AGENTS.md` (per-conversation prompt caching is sacred; the
core is a narrow waist). Don't drift to synonyms the glossary explicitly avoids.

If the concept you need isn't in the glossary yet, that's a signal: either you're inventing language
the project doesn't use (reconsider) or there's a real gap (note it for `/domain-modeling`).

## Flag ADR conflicts

No ADRs exist yet (`docs/adr/` was absent as of `b7db40d8`), so today the standing decisions live in
the root `AGENTS.md` invariants and the `website/docs/developer-guide/` area docs. If your output
contradicts one of those, surface it explicitly rather than silently overriding:

> _Contradicts the narrow-waist invariant in the root `AGENTS.md`, but worth reopening because…_
