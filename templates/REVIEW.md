# Review instructions

Used by Claude Code's code review: the managed Code Review service on pull requests, and the
sdlc plugin's built-in `code` reviewer.

## What Important means here
Reserve Important for findings that would break behaviour, lose or leak data, or block a
rollback. Style, naming and refactoring suggestions are Nit at most.

## Cap the nits
Report at most five Nits per review.

## Do not report
- Anything CI already enforces: lint, formatting, type errors
- Generated files and lockfiles

## Always check
- New behaviour has a test
