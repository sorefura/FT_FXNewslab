# Swap Bot Local Instructions

These instructions refine the repository root guidance for Live Trading work.

- Strategy produces Trade Candidates, never Broker orders.
- Portfolio evaluates Pair and Currency Exposure before Risk.
- Hard safety constraints belong to Risk.
- Execution accepts approved intents only.
- Do not call LLM providers from Risk or Execution.
- Do not import Research evaluators into the Live decision path.
- Preserve idempotency for order submission.
- Unknown swap data is not zero.
- Manual swap overrides must retain source identity and effective period.
- Behavior-changing migrations should support shadow mode, dry run, or decision diffing when practical.
- Test names must express What is guaranteed.
- Comments are allowed only for Why not constraints.
