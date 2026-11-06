---
name: help
description: Explain the sdlc commands, pipeline flow, how to add reviewers, and config. Use for /sdlc:help or "how do I use sdlc".
argument-hint: "[commands|flow|reviewers|config]"
disable-model-invocation: true
allowed-tools: Read
---

Help content:

!`cat "${CLAUDE_PLUGIN_ROOT}/reference/help.md"`

Topic requested: `$ARGUMENTS`

If a topic was given (commands, flow, reviewers, config), show only that section. Otherwise show
the Commands table and Flow, then list the other topics. Keep the commands/tables as written.
End with: "Next: /sdlc:init (new repo) or /sdlc:start <feature>".
