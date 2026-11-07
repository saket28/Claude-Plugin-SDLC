# sdlc help

A feature goes through: **spec → build → verify → review → merge**. You only step in at gates and when a job is `needs-human`.

## Commands

| Command | Use it to | Example |
|---|---|---|
| `/sdlc:help [topic]` | Show this help. Topics: `commands`, `flow`, `reviewers`, `config` | `/sdlc:help reviewers` |
| `/sdlc:init` | Set up a repo once: writes `SDLC.md`, `REVIEW.md`, `.sdlc/` | `/sdlc:init` |
| `/sdlc:intake <text>` | Park a feature in the backlog without starting it | `/sdlc:intake Add CSV export` |
| `/sdlc:start [id \| text]` | Start the next backlog item, a new feature, or resume/retry a job | `/sdlc:start Add rate limiting` |
| `/sdlc:status [id] [--check]` | See jobs, stages, tokens, and what is waiting on you. `--check` is a health check | `/sdlc:status --check` |
| `/sdlc:approve <id> <gate>` | Pass a gate: `spec`, `merge`, `budget +500k`, `override`, `retry`; or `learnings` | `/sdlc:approve 12 spec` |
| `/sdlc:rework <id> "<feedback>"` | Send a job back to the builder with your feedback | `/sdlc:rework 12 "Handle empty input"` |
| `/sdlc:cancel <id>` | Stop a job and remove its worktree (record and branch are kept) | `/sdlc:cancel 12` |
| `/sdlc:add-reviewer [name]` | Add a reviewer through a guided flow (file, subagent or command) | `/sdlc:add-reviewer a11y` |
| `/sdlc:review [names] [--base b]` | Run the named reviewers (default all) on the current branch, no job needed, and get one merged report | `/sdlc:review code a11y` |
| `/sdlc:dashboard` | Open the local dashboard: board, timelines, tokens, settings, help | `/sdlc:dashboard` |

## Flow

1. `spec`: the spec-writer writes `spec.md` with testable acceptance criteria. Spec reviewers (`at: spec`) check it. Gate `spec` (auto unless there are open questions).
2. `build`: the builder implements it in its own worktree (`<repo>.sdlc-worktrees/job-<n>`).
3. `verify`: your `commands.*` run, and an independent tester writes and runs acceptance tests. Failures go back to the builder.
4. `review`: reviewers with `at: review` run in parallel. Any **Important** finding sends the job back to the builder, up to `max_rounds`, then `needs-human`.
5. `merge`: reviewers with `at: merge`, CI, rules, then the `merge` gate (default: human).

When a job is `needs-human` or `failed`, `/sdlc:status` shows the command that moves it forward.

## Reviewers

Two are built in: `code` (Claude Code `/code-review`, using `REVIEW.md`) and `security` (`/security-review`, when `risk>=medium`). Add your own under `reviewers:` in `SDLC.md`. Easiest: run `/sdlc:add-reviewer`.

Each reviewer uses exactly ONE kind:

| Kind | Key | Use it for | Cost |
|---|---|---|---|
| Instructions | `instructions: review-a11y.md` | Most custom reviews: write what to check in plain markdown | tokens |
| Agent | `agent: perf-reviewer` | Needs its own tools, model or long prompt: `.claude/agents/perf-reviewer.md` | tokens |
| Command | `run: "npx license-checker --failOn GPL"` | Deterministic checks. Exit 0 = PASS, anything else = CHANGES with the output | none |

Fields (all optional except the kind):

| Field | Default | Meaning |
|---|---|---|
| `at` | `review` | When it runs: `spec` (reviews the spec before any code), `review` (after verify), `merge` (final diff) |
| `when` | `always` | Trigger rule: `always`, `never`, `touches-ui`, `risk>=low\|medium\|high`, `paths:[globs]`, joined with `or` |
| `condition` | none | Plain-language trigger judged per job, e.g. `"changes a public API contract"` |
| `blocking` | `true` | `false` = advisory: findings are recorded but never block |
| `model`, `effort` | agent default | Per-reviewer overrides |

A reviewer runs when its `when` matches **or** its `condition` is judged true. With neither, it always runs.

Example `SDLC.md` frontmatter:

```yaml
reviewers:
  a11y:
    instructions: review-a11y.md
    when: touches-ui
  architecture:
    at: spec
    instructions: review-architecture.md
    condition: "Adds a service or data store, or changes a public API contract"
  licenses:
    run: "npx license-checker --failOn GPL"
    at: merge
    when: "paths:[package.json]"
  security:
    when: "risk>=low or paths:[src/auth/**]"   # tweak a built-in: change when it runs
  code: { when: never }                        # turn a built-in off
```

An instructions file (`review-a11y.md`, repo root) says what to check, what is Important vs Nit, and what to ignore. A starter is in `templates/review-template.md`. Run `/sdlc:status --check` after editing to validate. Agents may not edit `SDLC.md` or `review-*.md` during a job.

## Config

Everything repo-specific is in the frontmatter of `SDLC.md`, and only values that differ from defaults are stored. Edit it by hand, or in the dashboard Settings tab (it shows a diff before saving). Common settings: `commands.*`, `gates.spec|merge`, `max_rounds`, `models.*`, `budgets.*`, `reviewers`, `learning`, `protected_paths`. Full list: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sdlc.py config`.
