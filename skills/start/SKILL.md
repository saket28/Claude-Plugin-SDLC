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

Then run the loop below until the action is `wait`, `done` or `failed`. Keep your own
messages short and don't open artifact files unless an action needs it: the scripts do the
bookkeeping, and your tokens count against the job's budget.

1. Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/sdlc.py" next <job> --session ${CLAUDE_SESSION_ID}`. It prints one JSON action.
2. Do exactly that action:
   - **spawn**: in ONE message, launch every entry of `agents` with the Agent tool
     (`subagent_type` = `agent`, `prompt` = `prompt`, verbatim). Wait for all of them. Don't read
     their output files. Go to step 1.
   - **ask**: answer each question from SDLC.md (Answers, Learnings) and CLAUDE.md where you can.
     Ask the user only what's left, in one AskUserQuestion call. Write all answers to
     `.sdlc/job-<job>/answers-input.md` as "Q: … / A: …" pairs, then run
     `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/sdlc.py" report <job> --answers .sdlc/job-<job>/answers-input.md`. Go to step 1.
   - **convert**: read `raw`, write `output` in the review format (frontmatter `verdict`,
     `important`, `nit`, then `## Important` and `## Nit` lists). Go to step 1.
   - **wait**: tell the user the `reason` and `hint` in one or two lines. If `waiter` is set, run it
     with Bash in the background; when it finishes, go to step 1. Otherwise stop.
   - **done** or **failed**: tell the user the result in one or two lines and stop.
