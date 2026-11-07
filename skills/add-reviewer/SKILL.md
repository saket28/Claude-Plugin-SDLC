---
name: add-reviewer
description: Add or change an sdlc reviewer (instructions file, project subagent, or shell command) through a guided flow.
argument-hint: "[reviewer-name]"
disable-model-invocation: true
allowed-tools: Read, Write, Edit, AskUserQuestion, Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/sdlc.py" *)
---

Add a reviewer to this repo's `SDLC.md`. Reviewer reference:

!`sed -n '/^## Reviewers/,/^## Config/p' "${CLAUDE_PLUGIN_ROOT}/reference/help.md"`

Current reviewers: !`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/sdlc.py" config get reviewers || true`

Name given: `$ARGUMENTS`

1. Ask in ONE AskUserQuestion call (skip what the name already answers): what it should check;
   kind (Instructions file / Project subagent / Shell command); when it runs (`at`: spec, review,
   merge); trigger (`when` rule and/or plain-language `condition`); blocking or advisory.
2. Create what the kind needs:
   - **instructions**: write `review-<name>.md` at the repo root from
     `${CLAUDE_PLUGIN_ROOT}/templates/review-template.md`, filled in with concrete checks from
     the user's answers.
   - **agent**: write `.claude/agents/<name>.md` (frontmatter `name`, `description`, `tools`
     read-only such as Read, Grep, Glob, Bash; then a prompt that ends by writing the review
     file in the format of the plugin's reviewer agent: `verdict`, `important`, `nit`).
   - **run**: nothing to create; confirm the command exits 0 on success.
3. Add the entry under `reviewers:` in the `SDLC.md` frontmatter, keeping existing content and
   comments. Use exactly one of `instructions`, `agent`, `run`.
4. Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/sdlc.py" status --check`. Fix any error it
   reports for this reviewer.
5. Show the user what was added (files and the `SDLC.md` entry) in a few lines. Do not commit.
