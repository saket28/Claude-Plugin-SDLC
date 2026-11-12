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
Agents talk to each other in a `## Comments` section, one line per comment:
`<id|new> -> <to>, <type>[, <severity>]: <text>` (indented lines are detail).
- `type`: `feedback` (raise an issue; severity `blocking` or `nit`), `pushback`, `fixed` (builder), `accept`, `ruling` (spec-writer: `keep` or `change`).
- `to`: `builder`, `tester`, `<name>-reviewer`, `spec-writer`. Unknown names are redirected.
- New issues use `new`; the director numbers them `I1`, `I2` … and keeps them in `threads.jsonl` (readable copy: `conversation.md`).
- The raiser's `accept` closes an issue; a nit pushback closes it; the raiser repeating a pushback is a stalemate (human); a checker that says nothing about an issue addressed to it closes it as "no reply".
