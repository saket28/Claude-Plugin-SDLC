---
name: rework
description: Give your ruling on an SDLC job's open issues: the builder applies it (add --spec to change the spec first).
argument-hint: "<job-id> [--spec] <ruling>"
disable-model-invocation: true
allowed-tools: Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/sdlc.py" *)
---

!`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/sdlc.py" rework $ARGUMENTS || true`

Confirm in one line, then continue the job now (the user just gave the feedback, so don't ask)
by running the loop below for that job, starting at its step 1.

In the loop, `<plugin-root>` is `${CLAUDE_PLUGIN_ROOT}` and `<session-id>` is `${CLAUDE_SESSION_ID}`.

!`cat "${CLAUDE_PLUGIN_ROOT}/reference/run-loop.md"`
