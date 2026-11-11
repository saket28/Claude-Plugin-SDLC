# sdlc: a Claude Code plugin for spec → build → verify → review → merge

`sdlc` takes a feature from a one-line description to merged code. A spec-writer agent writes a spec with testable acceptance criteria. A builder implements it in its own git worktree. Your test commands and an independent tester verify it. Claude Code's `/code-review` (using your `REVIEW.md`) and `/security-review`, plus any reviewers you define, review it. Then it merges, with a human gate if you want one. Every job is recorded in `.sdlc/job-<n>/`, token budgets are enforced, and a retro after each job writes learnings back into `SDLC.md`, so the next job avoids the same mistakes.

Status: **v0.1, in development.** See [INSTRUCTIONS.md](INSTRUCTIONS.md) for the full design.

## Quick start

```text
/plugin install sdlc@<marketplace>     # or: claude --plugin-dir /path/to/this/repo
/sdlc:init                             # detects your commands and writes SDLC.md, REVIEW.md, .sdlc/
/sdlc:start Add rate limiting to the login endpoint
/sdlc:status
```

## Commands

| Command | What it does |
|---|---|
| `/sdlc:init` | Set up the repo: `SDLC.md`, `REVIEW.md`, `.sdlc/` (gitignored), with `gh` set up if you use GitHub |
| `/sdlc:intake <text>` | Add a feature to the backlog |
| `/sdlc:start [id \| text]` | Start the next backlog item, a backlog card by its number, a new feature, or continue a job |
| `/sdlc:status [id] [--check]` | Jobs, stages, tokens, and anything waiting on you; `--check` runs a health check |
| `/sdlc:approve <id> <gate>` | Pass a gate: `spec`, `merge`, `budget +500k`, `override`, `retry` |
| `/sdlc:rework <id> "<feedback>"` | Send a job back to the builder with your feedback |
| `/sdlc:cancel <id>` | Cancel a job, keeping its record |
| `/sdlc:review [names] [--base b]` | Run the named reviewers (default all) on the current branch; one consolidated report |
| `/sdlc:dashboard` | Open the local dashboard (board, timelines, tokens, settings, help) |
| `/sdlc:help [topic]` | Explain commands, flow, reviewers and config (`commands`, `flow`, `reviewers`, `config`) |

## Adding a reviewer

Each reviewer is a markdown file in `.claude/reviewers/`: its frontmatter holds its settings (`at`, `when`, `condition`, `blocking`, `model`, and `agent:` or `run:` for those types) and its body is the instructions. `SDLC.md` just lists the paths: `review.reviewer.list: [.claude/reviewers/code.md, .claude/reviewers/security.md]`. `/sdlc:init` creates `code.md` and `security.md` (Claude Code's built-in `/code-review` and `/security-review`, both using `REVIEW.md`). Add or edit reviewers in the dashboard: `/sdlc:dashboard` → Settings → Review → **+ Add reviewer**; the file is created from a template with the right sections. Details and examples: `/sdlc:help reviewers` or [reference/help.md](reference/help.md).

## Configuration

Principles (how the repo does things: architecture, design system, personas, security, testing) live in `.claude/principles/`, which `/sdlc:init` seeds with empty starters; every agent is pointed at the ones that apply to its role. Everything repo-specific lives in `SDLC.md`, grouped as `global` plus one section per stage (`spec`, `implement`, `test`, `review`, `release`): commands, gates, budgets, critical-thinking levels, extra reviewers and when they run, integrations, pipeline rules, and the Answers and Learnings the retro maintains. `init` writes only what differs from the defaults. A repo can add reviewers of three kinds: an instructions file, a Claude Code project subagent, or a shell command.

## Requirements

- Claude Code with plugin support, Python 3.9+, git
- `gh` for GitHub pull requests (optional; without it the plugin merges locally)

## Development

```bash
cd tests && python3 -m unittest            # unit + end-to-end tests with simulated agents, no model calls
claude plugin validate .                   # manifest and component checks

# Evals run real models on your account. Export the claude path so headless reviews work in the sandbox.
export EVAL_SDLC_CLAUDE_BIN="$(command -v claude)"
claude plugin eval . --case status-check --scaffold --ablation none \
  --allow-tools "Bash(python3 *)"                                        # cheap (haiku)
claude plugin eval . --tag live --scaffold --ablation none --runs 1 \
  --allow-tools Write Edit "Bash(python3 *)" "Bash(git *)" "Bash(cd *)"   # full jobs, ~$1-2 each
```
