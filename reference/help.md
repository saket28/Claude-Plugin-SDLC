# sdlc help

A feature goes through: **spec → build → verify → review → merge**. You only step in at gates and when a job is **human required** (it shows the alert icon on its card, in whatever stage it stopped).

## Commands

| Command | Use it to | Example |
|---|---|---|
| `/sdlc:help [topic]` | Show this help. Topics: `commands`, `flow`, `reviewers`, `config` | `/sdlc:help reviewers` |
| `/sdlc:init` | Set up a repo once: writes `SDLC.md`, `REVIEW.md`, `.sdlc/` | `/sdlc:init` |
| `/sdlc:intake <text>` | Park a feature in the backlog without starting it | `/sdlc:intake Add CSV export` |
| `/sdlc:start [id \| text]` | Start the next backlog item, a backlog card by its number (`/sdlc:start 6`), a new feature, or resume/retry a job | `/sdlc:start Add rate limiting` |
| `/sdlc:status [id] [--check]` | See jobs, stages, tokens, and what is waiting on you. `--check` is a health check | `/sdlc:status --check` |
| `/sdlc:approve <id> <gate>` | Pass a gate: `spec`, `merge`, `budget +500k`, `override`, `retry` | `/sdlc:approve 12 spec` |
| `/sdlc:rework <id> "<feedback>"` | Send a job back to the builder with your feedback | `/sdlc:rework 12 "Handle empty input"` |
| `/sdlc:cancel <id>` | Stop a job and remove its worktree (record and branch are kept) | `/sdlc:cancel 12` |
| `/sdlc:review [names] [--base b]` | Run the named reviewers (default all) on the current branch, no job needed, and get one merged report | `/sdlc:review code a11y` |
| `/sdlc:dashboard` | Open the local dashboard: board, timelines, tokens, settings, help | `/sdlc:dashboard` |

## Flow

1. `spec`: the spec-writer writes `spec.md` with testable acceptance criteria. Spec reviewers (`at: spec`) check it. Gate `spec` (off by default; set `spec.gate.mode: human` to approve specs yourself).
2. `build`: the builder implements it in its own worktree (`<repo>.sdlc-worktrees/job-<n>`).
3. `verify`: your `global.command` settings (lint, build, test) run, and an independent tester writes and runs acceptance tests. Failures go back to the builder.
4. `review`: the tests and reviewers with `at: review` look at the code and raise issues (a **round**). The builder and checkers then answer each other on each issue, up to `global.pipeline.max_exchanges` times (default 2); what is still open goes to you. When a round's issues are all closed, the code is reviewed again, up to `global.pipeline.max_rounds` rounds (default 3).
5. `merge`: reviewers with `at: merge`, CI, rules, then the `merge` gate (default: human).

When a job is human required (a decision, a stalemate between agents, a failure), `/sdlc:status` shows the command that moves it forward.

## Reviewers

Each reviewer is a markdown file in `.claude/reviewers/`. Its **frontmatter holds all its settings** and its **body is its instructions**; `SDLC.md` only lists the paths:

```yaml
# SDLC.md
review:
  reviewer:
    list: [.claude/reviewers/code.md, .claude/reviewers/security.md, .claude/reviewers/a11y.md]
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

`/sdlc:init` creates `code.md` and `security.md` as the starting reviewers. Add, edit or remove reviewers in the dashboard: `/sdlc:dashboard` → Settings → Review. Saving writes the file straight away. Reviewers still written inline under `review.reviewer.list:` as a map keep working; the dashboard (or `sdlc.py reviewers convert`) converts them to files. Run `/sdlc:status --check` after editing by hand. Agents may not edit `SDLC.md`, `review-*.md`, `.claude/reviewers/**` or `.claude/agents/**` during a job.

## Principles

Principles say how the repo does things: architecture, design system, personas, security, testing. They are markdown files in `.claude/principles/`; `/sdlc:init` creates empty starters. Fill them in, add your own, or edit and remove them in the dashboard: `/sdlc:dashboard` → Settings → Global → Principles. Each file's frontmatter has a `summary`, `applies_to` (which agents see it; empty = all) and an optional `when` (for example `touches-ui`). Agents are pointed at the applicable ones in their brief and read what is relevant. Files with no text yet are skipped. Agents can't edit them.

## Critical thinking

Agents can question their instructions before acting, and push back with evidence instead of complying silently. The level is a setting (`global.pipeline.critical_thinking`, default 1); each stage (`<stage>.agent.critical_thinking`) and each reviewer can set their own. Higher levels cost more tokens and rounds.

| Level | Name | What the agent does |
|---|---|---|
| 0 | Off | Follows instructions as given. Nothing is added to its brief. |
| 1 | Flag | Raises an objection only when an instruction clearly contradicts the spec, the code or the repo's rules. |
| 2 | Check | Checks instructions against the spec, the code and the rules; flags contradictions and gaps. |
| 3 | Challenge | Questions instructions, specs and earlier findings. Pushes back on weak points, with evidence for each objection. |
| 4 | Sceptical | Assumes there are mistakes until checked. Pushes back early and says what would change its mind. |
| 5 | Adversarial | Challenges every assumption, including the task itself. Proposes a better approach before starting. |

Agents talk in threads, one per issue, shown under Conversation in the job detail and in `conversation.md`. A reviewer or the tester raises an issue (blocking or nit), the builder fixes it or pushes back, and the raiser accepts or pushes back again. A plain-code director routes each comment, closes an issue when its raiser accepts, and sends it to you when the two sides still disagree (a stalemate) or the rounds run out. A checker can also ask the spec-writer to rule when the spec itself is wrong. You answer with `approve override` (the work stands), `rework "ruling"` (the builder applies it as the final exchange) or `rework --spec "ruling"` (the spec-writer changes the spec first, then the builder and tester follow). Issues are grouped by round.

## Config

Everything repo-specific is in the frontmatter of `SDLC.md`, and only values that differ from defaults are stored. Edit it by hand, or in the dashboard Settings tab (it shows a diff before saving). Every setting is named `<tab>.<section>.<setting>` (three levels in `SDLC.md`), and the dashboard shows it by the last part under a block for its section. The tabs are `global` and one per stage: `spec`, `implement`, `test`, `review`, `release`. Each stage has an `agent` section (`model`, `critical_thinking`: 0–5, default 1 from `global.pipeline`, how hard it questions its instructions) and a `budget` section (`tokens`). Others: `spec.gate.mode` and `release.gate.mode` (`none` or `human`), `spec.format.sections`, `test.scope.paths`, `test.verify.*`, `review.severity.nits_block`, `review.reviewer.list`, `release.merge.*` (strategy, message, delete_branch), `release.pr.*` (draft, labels, reviewers), `release.ci.required`, `release.cleanup.worktree`, `release.host`. `global` holds `pipeline` (`max_rounds`, `critical_thinking`, `judge_model`, `agent_attempts`), `learning` (`mode`, `on`), `repo` (branches, `branch_name`, folders), `command` (`setup`, `build`, `lint`, `test`, `timeout`), `board`, `budget`, `dashboard.stall_after`, `wait` (`interval`, `max_hours`), `guardrail` and `principle.list`. Full list: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sdlc.py config`.
