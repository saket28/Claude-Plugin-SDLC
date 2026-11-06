# <Name> review

<One sentence: what this reviewer is responsible for.>

## Important (blocks merging)
Reserve Important for findings that would <break behaviour / violate a requirement / etc.>.
- <Concrete thing to check>
- <Concrete thing to check>

## Nit (recorded, never blocks)
- <Style or polish issues in this area>

## Do not report
- Issues the change did not introduce
- Anything CI or a linter already enforces

## How to look
- Start from `git diff <base>...HEAD`; read surrounding code only as needed.
- Check each acceptance criterion in the spec that relates to this area.
