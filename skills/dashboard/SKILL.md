---
name: dashboard
description: Open the local SDLC dashboard (board, job timelines, token usage, settings editor).
disable-model-invocation: true
allowed-tools: Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/sdlc.py" *)
---

Start the dashboard server with the Bash tool **in the background** (it keeps running):

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/sdlc.py" serve --open
```

Its first output line is JSON with the `url` (it also saves it to `.sdlc/dashboard.url`). Give the
user that URL in one line. It only listens on 127.0.0.1 and the URL contains a private token, so
don't paste it anywhere else. Then stop.
