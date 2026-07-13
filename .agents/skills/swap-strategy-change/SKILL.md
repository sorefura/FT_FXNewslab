---
name: swap-strategy-change
description: Use when changing Swap Bot strategy scoring, carry rules, signal combination, candidate generation, portfolio sizing, exposure checks, or live risk boundaries.
---

# Swap Strategy Change

1. Read `docs/04_SWAP_BOT.md`.
2. Read `docs/02_DOMAIN_MODEL.md`.
3. Decide whether the change belongs to Strategy, Portfolio, Risk, or Execution before editing.
4. Strategy may produce `TradeCandidate`; it may not place Broker orders.
5. Portfolio must evaluate aggregated Currency Exposure.
6. Hard safety limits belong to Risk, not weighted Strategy scoring.
7. Execution only accepts approved intent and must not reinterpret market Signals.
8. Preserve Strategy and policy version information.
9. Add tests whose names state the What.
10. When changing a threshold or weight, explain Why in the commit message.
11. Use comments only when an obvious alternative must not be used.
12. For behavior-changing rollout, prefer shadow mode, dry run, decision diff logging, or limited pair scope.

Before completion, verify that no Broker SDK import was added to Strategy or Portfolio.
