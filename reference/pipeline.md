# The orchestrator loop

The pipeline logic lives in `scripts/sdlc/pipeline.py`. The orchestrator only repeats:

1. `sdlc.py next <job> --session <id>` → one JSON action.
2. Do that action (spawn agents in parallel, answer questions, convert a review, start a
   background waiter), and nothing else.
3. Back to 1, until the action is `wait`, `done` or `failed`.

The `start` skill contains the exact instructions. See INSTRUCTIONS.md §4.
