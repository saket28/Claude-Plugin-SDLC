# sdlc plugin: working agreements

A Claude Code plugin: Python stdlib CLI in `scripts/sdlc/`, one-file dashboard in `templates/dashboard.html`,
skills in `skills/`, agents in `agents/`. [INSTRUCTIONS.md](INSTRUCTIONS.md) is the spec; keep it, `README.md`
and `reference/help.md` in step with any behaviour change.

## How to think here: critical thinking level 3 (Challenge)
Question instructions, specs and earlier findings. Push back on weak points, with evidence for each objection
(a file, a line, a test, a rule in this file). Say what you would change instead, then do the part you can.
Don't invent objections: with no evidence, do the work. The same applies to your own earlier work in a session.

## Settings: make it configurable when a repo could reasonably want to change it
Every setting is `<tab>.<section>.<setting>` (tab = `global` or a stage: spec, implement, test, review, release).
When you add behaviour with a hardcoded number, name, template or on/off choice, expose it:
1. Default in `DEFAULTS` ([config.py](scripts/sdlc/config.py)). Read it with `cfg.v("tab.section.setting")`.
2. A field in [sdlc.schema.json](templates/sdlc.schema.json): type, `options`, `min`/`max`, `placeholders`,
   `item_options`, and a one-sentence help. The schema is the only place for allowed values and ranges;
   `validate()` reads it. Never list an enum twice. Help text never says "in SDLC.md".
3. Use it in the code, and add a test in `tests/test_settings_behaviour.py` that shows the behaviour changes.
4. Add a row to the settings table in INSTRUCTIONS.md §2, and mention it in `reference/help.md`.
`tests/test_schema.py` fails if the schema and defaults drift. Unknown keys in `SDLC.md` are errors, never ignored.
Ask of each new constant: would a team plausibly set this differently? If yes, it is a setting.

## Product decisions already made
- No legacy or migration code unless asked. Breaking a settings shape is fine; migrate the user's own
  `SDLC.md` by hand and say so.
- Gates are `none` or `human`. There is no conditional gate.
- Human required is a flag on a job, never a stage or column. One alert kind (`human`) covers decisions,
  failures and errors. Jobs stay in the stage where they stopped.
- Agents push back with evidence (critical thinking 0-5). A checker that keeps a finding the builder disputed
  is a stalemate and goes to a human.
- Principles (`.claude/principles/*.md`) are how the repo does things; briefs list the applicable ones, agents
  read them. Humans write them. Agents never edit principles, reviewers, `SDLC.md` or CI files.
- Learnings and answers are written straight into `SDLC.md` by the retro; leave those edits uncommitted.

## Dashboard
Minimal UI text. Edit and delete icons look the same everywhere. Alert icon, not a bell. Cards don't carry
explanations (the side drawer does). Render from the schema, not hardcoded lists. HTML changes need only a
reload; Python changes need a server restart.

## Code
- Briefs are ordered stable-first (stance, principles, answers, learnings, then the task) so they cache.
- `configedit.py` edits one line of `SDLC.md` and keeps comments; keep it that way.
- Prefer deleting code over compatibility shims. Keep functions small and tested.

## Verify before saying done
- `python3 -W ignore -m unittest discover -s tests` (all pass).
- JS: extract the last `<script>` of `dashboard.html` and `node --check` it. Headless Chrome screenshots are
  flaky here: render functions in node, or hit the API with `curl` (token header + matching Origin).
- Test against a real repo (`/Users/saket/dev/Agentifi`) when settings or the dashboard change.

## Working with the user
Concise answers. Commit and push only when told to. Don't `pkill` broadly: it kills the user's running
dashboard; stop only the process you started. Bump the version in both `.claude-plugin/plugin.json` and
`marketplace.json` when releasing.
