---
builtin: security-review
at: review
when: "risk>=medium"
blocking: true
---
<!--
Runs Claude Code's built-in /security-review on the job's diff. The rules in REVIEW.md are
appended to it too, so a "Security" section there applies here.
- When it runs: the settings above (when: always runs it on every job).
- Anything written outside this comment is added as extra instructions for this review.
-->
