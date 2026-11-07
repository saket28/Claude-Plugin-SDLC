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
| `/sdlc:approve <id> <gate>` | Pass a gate: `spec`, `merge`, `budget +500k`, `override`, `retry` | `/sdlc:approve 12 spec` |
| `/sdlc:rework <id> "<feedback>"` | Send a job back to the builder with your feedback | `/sdlc:rework 12 "Handle empty input"` |
| `/sdlc:cancel <id>` | Stop a job and remove its worktree (record and branch are kept) | `/sdlc:cancel 12` |
| `/sdlc:review [names] [--base b]` | Run the named reviewers (default all) on the current branch, no job needed, and get one merged report | `/sdlc:review code a11y` |
| `/sdlc:dashboard` | Open the local dashboard: board, timelines, tokens, settings, help | `/sdlc:dashboard` |

## Flow

1. `spec`: the spec-writer writes `spec.md` with testable acceptance criteria. Spec reviewers (`at: spec`) check it. Gate `spec` (auto unless there are open questions).
2. `build`: the builder implements it in its own worktree (`<repo>.sdlc-worktrees/job-<n>`).
3. `verify`: your `commands.*` run, and an independent tester writes and runs acceptance tests. Failures go back to the builder.
4. `review`: reviewers with `at: review` run in parallel. Any **Important** finding sends the job back to the builder, until `max_rounds` total rounds (default 3, the first build included) are used, then `needs-human`.
5. `merge`: reviewers with `at: merge`, CI, rules, then the `merge` gate (default: human).

When a job is `needs-human` or `failed`, `/sdlc:status` shows the command that moves it forward.

## Reviewers

Each reviewer is a markdown file in `.claude/reviewers/`. Its **frontmatter holds all its settings** and its **body is its instructions**; `SDLC.md` only lists the paths:

```yaml
# SDLC.md
reviewers: [.claude/reviewers/code.md, .claude/reviewers/security.md, .claude/reviewers/a11y.md]
```

```markdown
---
at: review                   # spec | review | merge: when in the pipeline it runs
when: touches-ui             # always, never, touches-ui, risk>=low|medium|high, paths:[globs], joined with `or`
condition: "Changes a form"  # optional plain-language trigger, judged per job
blocking: true               # false = advisory: findings are recorded but never block
model: sonnet                # optional: opus | sonnet | haiku | fable
---
# Accessibility review
What to check, what is Important (blocks) vs Nit (never blocks), what to ignore.
```

A reviewer runs when its `when` matches **or** its `condition` is judged true. With neither, it always runs.

The type comes from one extra frontmatter key, or none:

| Type | Frontmatter | Use it for | Cost |
|---|---|---|---|
| Instructions | none (the body is the instructions) | Most custom reviews: UX, marketing, accessibility, architecture | tokens |
| Agent | `agent: perf-reviewer` (definition in `.claude/agents/perf-reviewer.md`) | Needs its own tools or a long, specialised prompt | tokens |
| Command | `run: "npx license-checker --failOn GPL"` | Deterministic checks. Exit 0 = PASS, anything else = CHANGES with the output | none |
| Built-in | `builtin: code-review` or `security-review` | Claude Code's own reviews. They apply `REVIEW.md`; text in the body (outside `<!-- -->` comments) is added as extra instructions | tokens |

`/sdlc:init` creates `code.md` and `security.md` as the starting reviewers. Add, edit or remove reviewers in the dashboard: `/sdlc:dashboard` → Settings → Reviewers. Saving writes the file straight away. Reviewers still written inline under `reviewers:` as a map keep working; the dashboard (or `sdlc.py reviewers convert`) converts them to files. Run `/sdlc:status --check` after editing by hand. Agents may not edit `SDLC.md`, `review-*.md`, `.claude/reviewers/**` or `.claude/agents/**` during a job.

## Config

Everything repo-specific is in the frontmatter of `SDLC.md`, and only values that differ from defaults are stored. Edit it by hand, or in the dashboard Settings tab (it shows a diff before saving). Common settings: `commands.*`, `gates.spec|merge`, `max_rounds`, `merge_strategy` (`squash` default, `merge`, `rebase`), `models.*`, `budgets.*`, `reviewers`, `learning`, `protected_paths`. Full list: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sdlc.py config`.
