---
name: start
description: Start or continue an SDLC job and run it through spec, build, verify, review and merge.
argument-hint: "[job-id | feature description]"
disable-model-invocation: true
allowed-tools: Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/sdlc.py" *)
---

Start or continue a job:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/sdlc.py" start $ARGUMENTS
```

Then run the loop:

In the loop, `<plugin-root>` is `${CLAUDE_PLUGIN_ROOT}` and `<session-id>` is `${CLAUDE_SESSION_ID}`.

!`cat "${CLAUDE_PLUGIN_ROOT}/reference/run-loop.md"`
