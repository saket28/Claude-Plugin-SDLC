#!/usr/bin/env bash
# Seed the workspace with the todo-app fixture as a git repo set up for sdlc.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
cp -R "$here/../fixtures/todo-app/." .
git init -q -b main
git -c user.email=eval@example.com -c user.name=eval add -A
cat > SDLC.md <<EOF2
---
global: { command: { test: "python3 -m unittest discover -s tests -t ." } }
test: { scope: { paths: ["tests/**"] } }
release: { gate: { mode: none }, host: { adapter: local } }

---

## Pipeline rules
- Keep changes small.
EOF2
printf ".sdlc/\n__pycache__/\n" >> .gitignore
git add -A
git -c user.email=eval@example.com -c user.name=eval commit -qm init
git config user.email eval@example.com
git config user.name eval
