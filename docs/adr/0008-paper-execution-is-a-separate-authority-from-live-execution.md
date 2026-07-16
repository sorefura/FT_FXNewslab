# ADR 0008: Paper Execution Is a Separate Authority from Live Execution

## Status

Accepted on 2026-07-16. Implementation is planned by ExecPlan 0006.

## Context

ExecPlan 0005 proves that an explicitly adopted Signal can pass Strategy, Portfolio,
Risk, and approved-intent boundaries, but deliberately returns `NOT_SUBMITTED`.
Continuous operational testing now needs fictional orders, fills, positions, account
state, PnL, and swap accrual based on real-time public observations.

Extending `NOT_SUBMITTED` with counters cannot exercise order lifecycle, partial
fills, restart recovery, or ledger reconciliation. Conversely, treating a successful
Paper run as a variant of Live execution would let simulated evidence weaken the
separate authority required to expose Broker credentials and Private POST.

Paper fills and swap cash flows are model outputs, not Broker facts. Without exact
input evidence and versioned deterministic formulas, a Paper result could not be
replayed or compared after a model change. Using Research Forward Results would also
introduce future outcome data into an operational execution path.

## Decision

Live operations have three explicit modes:

```text
SHADOW_NOT_SUBMITTED
PAPER
LIVE
```

They are separate enum values and authorities, not one `dry_run` boolean.

`SHADOW_NOT_SUBMITTED` stops after approved intent and records no fictional order.
`PAPER` routes approved entry/close/liquidation intents only to a dedicated
`PaperExecutionGateway`. That adapter and its composition root do not import,
construct, or call the real Broker transport. `LIVE` is rejected throughout ExecPlan
0006 and requires a new explicit Controlled Live rollout decision in ExecPlan 0007.

```text
PAPER_EXECUTED != LIVE_EXECUTED
```

Paper success, burn-in evidence, or simulated profitability never creates Live
authority and never changes two-step Live arming.

Paper orders, lifecycle events, fills, positions, account snapshots, ledger entries,
PnL, swap accruals, cycles, and reconciliation results are append-oriented evidence.
Semantic identity excludes incidental audit time and uses canonical cryptographic
content identity.

The Paper fill model is versioned and deterministic. Its identity includes the exact
approved intent, post-intent available market observation, spread/slippage/liquidity
or partial-fill versions, and an explicit seed if randomness is used. Research
Forward Results and future observations are forbidden.

Swap accrual references exact versioned evidence including Pair, long/short values,
unit basis, settlement currency, effective/rollover period, source identity, captured
time, and accrual formula version. Missing or stale swap is not zero.

Paper account, position, and PnL state is rebuilt from append-only Decimal ledger
evidence and explicit formula versions. Recovery appends reconciliation evidence and
does not rewrite historical records.

## Consequences

- Paper operation can test realistic lifecycle, recovery, and accounting without
  widening external side effects.
- A real Broker adapter cannot be substituted into the ExecPlan 0006 composition by
  changing a boolean.
- Paper and real Broker adapters may share approved-intent vocabulary, but not
  transport construction or authority.
- Fill quality and PnL remain attributable to exact model/evidence versions rather
  than being mistaken for Broker truth.
- A future Live rollout must create its own operator authority, limits, kill switch,
  reconciliation, rollback, and acceptance evidence in ExecPlan 0007.
- More schema and recovery work is required than returning a synthetic
  `OrderResult`, but the resulting evidence is reproducible and auditable.
