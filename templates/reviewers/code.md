---
builtin: code-review
at: review
when: always
blocking: true
effort: high
---
<!--
Runs Claude Code's built-in /code-review on the job's diff, applying the rules in REVIEW.md.
- What it flags and how severe: edit REVIEW.md (the managed Code Review service reads it too).
- When it runs, how hard it looks (effort) and which model runs it: the settings above.
- Anything written outside this comment is added as extra instructions for this review.
-->
