---
name: approve
description: Pass a gate for an SDLC job (spec, merge, budget, override, retry) or accept pending learnings.
argument-hint: "<job-id> <spec|merge|budget +500k|override|retry> | learnings [ids]"
disable-model-invocation: true
allowed-tools: Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/sdlc.py" *)
---

!`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/sdlc.py" approve $ARGUMENTS || true`

Tell the user the result in one line. If a job can now continue, ask whether to continue it
now with /sdlc:start <job-id>.
