---
name: cancel
description: Cancel an SDLC job, keeping its folder and branch.
argument-hint: "<job-id>"
disable-model-invocation: true
allowed-tools: Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/sdlc.py" *)
---

!`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/sdlc.py" cancel $ARGUMENTS || true`

Confirm in one line.
