---
name: review
description: Run the named reviewers (default all) on the current branch and write one consolidated report. No job needed.
argument-hint: "[reviewer-names...] [--base <branch>]"
disable-model-invocation: true
allowed-tools: Read, Write, Bash, Agent, Skill
---

Review the current branch with the reviewers named in `$ARGUMENTS` (none named = every reviewer in this repo's `SDLC.md`), then produce one report.

Plan: !`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/sdlc.py" review-plan $ARGUMENTS || true`

If the plan is an `error`, show it and stop. If `files` is empty, say there is nothing to review
and stop. Otherwise start all reviewers **in parallel** (one message, several calls). Each writes
`<report_dir>/review-<name>.md` in the reviewer format
(`verdict: PASS|CHANGES`, `## Important`, `## Nit`). Never modify the code under review.

By `kind`:
- `builtin` / `code-review`: run the `code-review` skill on `<base>...HEAD` (REVIEW.md applies when `has_review_md`).
- `builtin` / `security-review`: run the `security-review` skill.
- `instructions`: spawn the `sdlc:reviewer` agent. Its prompt: the contents of the instructions
  file `target`, the diff command `git diff <base>...HEAD`, and the `output` path. No spec exists, so
  judge the change on its own merits.
- `agent`: spawn the named project agent with the same prompt.
- `run`: run the command `target` with Bash. Exit 0 = PASS, otherwise CHANGES with the output tail.

For built-in skills, convert their findings into the reviewer format with a severity of
Important (would break behaviour, lose or leak data, or break a rule in REVIEW.md) or Nit.
If a reviewer fails to run, record `verdict: ERROR` with the reason; do not stop the others.

Then write `<report_dir>/report.md`:

1. Header: branch vs base, number of files, date, and an overall verdict. **BLOCKED** if any
   `blocking` reviewer says CHANGES or ERROR, else **CLEAR** (note advisory findings).
2. A summary table: reviewer, kind, verdict, Important count, Nit count, blocking?
3. **Important findings**, merged across reviewers. Combine duplicates (same place, same issue)
   and name every reviewer that raised them. Order by risk. Each: `path:line`, the problem, why it
   matters, how to fix.
4. **Nits**, grouped by file, briefly.
5. Reviewers skipped (from `skipped`) and why.

Finish by showing the user the verdict, the table, the top Important findings (max 5), and the
report path. Do not commit anything and do not fix the findings unless asked.
