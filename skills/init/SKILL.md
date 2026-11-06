---
name: init
description: Set up the SDLC pipeline in this repository (SDLC.md, REVIEW.md, .sdlc/).
disable-model-invocation: true
allowed-tools: Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/sdlc.py" *)
---

Set up this repository for the sdlc pipeline.

Detected so far:
!`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/sdlc.py" init --detect || true`

1. Show the user what was detected (language, commands, host, test paths) in a short list.
2. Ask, in ONE AskUserQuestion call, only about what's missing or uncertain: the test, lint and
   build commands; whether merges need a human (default yes); and, if the host is `github`
   but `gh` is missing, whether to install it (`brew install gh` on macOS, `winget install
   GitHub.cli` on Windows, the distro package on Linux). If they install it, ask them to run
   `! gh auth login` themselves. If they decline, the host becomes `local`.
3. Write the settings that differ from the defaults (only those) as JSON to a temp file, for
   example `{"commands": {...}, "test_paths": [...], "host": {"adapter": "local"}}`, and run
   `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/sdlc.py" init --write <file>`. Record each answer the user gave under `## Answers` in SDLC.md
   as `- [A-xxxx] all · Question? Answer. (init)`.
4. Offer to turn on Claude Code's Bash sandbox in the user's settings, explaining that it limits
   where agents' shell commands can write. Only change settings if they agree.
5. Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/sdlc.py" status --check` and report the result. Finish with: "Next: /sdlc:intake or /sdlc:start".
