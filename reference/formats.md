# Artifact formats

Every artifact starts with YAML frontmatter (the restricted subset in INSTRUCTIONS.md §2).

| File | Required frontmatter |
|---|---|
| `spec.md` | `revision`, `risk` (low/medium/high), `touches_ui`, `change_type` (feat/fix/chore), `breaking`, `paths`, `open_questions` |
| `round-N/build.md` | `round` |
| `round-N/verify.md` | `verdict` (PASS/FAIL), `commands` (written by sdlc.py) |
| `round-N/test.md` | `verdict` (PASS/FAIL) |
| `review-<name>.md` | `verdict` (PASS/CHANGES), `important`, `nit` |
| `decision.md` | `decision` (MERGE/ESCALATE), `override` |

Sections every agent may add: `## Questions`, `## Learning candidates`.
Reviews list findings under `## Important` and `## Nit`, each as `path:line` + what + why + fix.
