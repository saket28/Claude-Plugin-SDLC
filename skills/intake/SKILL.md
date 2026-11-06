---
name: intake
description: Add a feature to the SDLC backlog.
argument-hint: "<feature description>"
disable-model-invocation: true
allowed-tools: Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/sdlc.py" *)
---

!`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/sdlc.py" intake $ARGUMENTS || true`

Confirm in one line what was added. Don't do anything else.
