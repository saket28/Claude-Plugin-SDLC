---
name: approve
description: Pass a gate for an SDLC job (spec, merge, budget, override, retry).
argument-hint: "<job-id> <spec|merge|budget +500k|override|retry>"
disable-model-invocation: true
allowed-tools: Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/sdlc.py" *)
---

!`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/sdlc.py" approve $ARGUMENTS || true`

Tell the user the result in one line. If the output says to run /sdlc:start <job-id> (the job
can now continue), don't ask: continue it now by running the loop below for that job, starting
at its step 1. For an error, just report.

In the loop, `<plugin-root>` is `${CLAUDE_PLUGIN_ROOT}` and `<session-id>` is `${CLAUDE_SESSION_ID}`.

!`cat "${CLAUDE_PLUGIN_ROOT}/reference/run-loop.md"`
