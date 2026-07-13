# FX Research Local Instructions

These instructions refine the repository root guidance for Research work.

- Treat every Signal as an immutable ex-ante record.
- Never use post-horizon market data in Signal production.
- Distinguish `published_at` from `first_seen_at`.
- Forward Result is a separate record from Signal.
- Metrics must be segmented by scorer/model version when version changes affect semantics.
- Always include sample count with sliced research metrics.
- Live PnL is not the direct label for Signal quality.
- Do not import Swap Bot execution, risk, or broker modules.
- Test names must express What is guaranteed.
- Comments are allowed only for Why not constraints such as leakage prevention or non-obvious timestamp choices.
