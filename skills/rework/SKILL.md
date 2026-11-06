---
name: rework
description: Send an SDLC job back to the builder with your feedback.
argument-hint: "<job-id> <feedback>"
disable-model-invocation: true
allowed-tools: Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/sdlc.py" *)
---

!`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/sdlc.py" rework $ARGUMENTS || true`

Confirm in one line, then ask whether to continue the job now with /sdlc:start <job-id>.
