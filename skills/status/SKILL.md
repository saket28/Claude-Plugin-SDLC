---
name: status
description: Show SDLC jobs, their stage, tokens and anything waiting on a human. --check runs the health check.
argument-hint: "[job-id] [--check]"
disable-model-invocation: true
allowed-tools: Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/sdlc.py" *)
---

!`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/sdlc.py" status $ARGUMENTS || true`

Summarize this for the user as a compact table (job, title, status, round, tokens). Call out
anything that is needs-human, failed or waiting, with the command that moves it forward.
For --check output, list the issues and offer to fix the simple ones.
