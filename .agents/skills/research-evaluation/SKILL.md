---
name: research-evaluation
description: Use for forward scoring, IC, hit rate, monotonicity, MFE/MAE, signal validation, research metrics, or future-leakage checks.
---

# Research Evaluation

1. Read `docs/03_SIGNAL_AND_RESEARCH.md`.
2. Confirm the Signal is an immutable ex-ante record.
3. Use `first_seen_at` for availability reasoning unless a stricter timestamp exists.
4. Confirm Horizon completion before finalizing Forward Result.
5. Keep Forward Result separate from Signal.
6. Report sample count with every sliced metric.
7. Evaluate at minimum:
   - Information Coefficient
   - Hit Rate
   - score bucket monotonicity
   - MFE
   - MAE
8. Slice by scorer version.
9. Do not mix versions into a single headline metric without an explicit combined analysis.
10. Check stability across time or market regime when sample size permits.
11. Add hand-calculated deterministic tests for metric functions.
12. Treat Live PnL as a separate downstream outcome, not the Signal label.

Code comments are reserved for Why not, especially leakage prevention or counterintuitive timestamp rules.
