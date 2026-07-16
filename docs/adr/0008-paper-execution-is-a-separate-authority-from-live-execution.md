# ADR 0008: Paper Execution Is a Separate Authority from Live Execution

## Status

Accepted on 2026-07-16 and clarified on 2026-07-17. Implementation is planned by
ExecPlan 0006.

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

The existing Adoption gate has only `RuntimeMode.SHADOW` and `RuntimeMode.LIVE`.
Paper execution is fictional and therefore cannot justify requesting the latter.
Execution authority and Signal-input authorization answer different questions and
must not be collapsed into one enum.

A deterministic result formula is insufficient if a restart may select different
cycle inputs or a different eligible market observation. Variable input IDs in a
cycle identity allow a restarted schedule slot to become a second logical cycle.
Likewise, recomputing a due boundary, seed, or quote selection can change Paper
history even if each individual calculation is deterministic.

One selection for an entire intent also cannot represent partial fill continuation:
the remaining quantity must be evaluated later against a distinct Step and market
window. Treating a pre-due PENDING check as immutable terminal no-market evidence
would create the opposite problem by preventing that same Step from resolving when a
quote arrives before its due boundary.

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

Execution authority maps to existing Adoption authorization as follows:

```text
SHADOW_NOT_SUBMITTED -> RuntimeMode.SHADOW
PAPER                -> RuntimeMode.SHADOW
LIVE                 -> RuntimeMode.LIVE
```

`SHADOW_ONLY` and `LIVE_ELIGIBLE` approvals may both feed a Paper cycle through
`RuntimeMode.SHADOW`. The cycle separately persists `ExecutionAuthorityMode.PAPER`.
No `RuntimeMode.PAPER` is added, and Paper cannot create a `RuntimeMode.LIVE`
authorization. The `LIVE` mapping is reserved for ExecPlan 0007 and is rejected by
ExecPlan 0006 before authorization or cycle claim.

```text
PAPER_EXECUTED != LIVE_EXECUTED
```

Paper success, burn-in evidence, or simulated profitability never creates Live
authority and never changes two-step Live arming.

Paper orders, lifecycle events, fills, positions, account snapshots, ledger entries,
PnL, swap accruals, cycles, and reconciliation results are append-oriented evidence.
Semantic identity excludes incidental audit time and uses canonical cryptographic
content identity.

One scheduled semantic `CycleSlot` is identified by UTC `scheduled_for`, UTC `as_of`,
execution authority, Strategy ID/version/config identity, and cycle-policy version.
Input IDs are not part of the slot ID. The first claim transaction freezes exactly
one immutable `CycleInputSnapshot`, including selected Signal/Pair Signal,
authorization/adoption, swap/market, Position/Account, checkpoint, selection-policy,
and freshness-policy lineage. Its semantic hash excludes first-write `captured_at`.
Retry reads the stored snapshot and appends a separate `CycleAttempt`; it never
reselects late, backfilled, or corrected inputs. A conflicting second snapshot fails
closed.

One approved intent first-writes exactly one immutable `FillEvaluationPlan`, fixing
original Decimal quantity, Pair/side, step-schedule and terminal boundaries, all
fill/selection/spread/slippage/liquidity/partial-fill/cancellation/expiry versions,
and seed root. The plan owns ordered `FillEvaluationStep` records with contiguous
ordinals. Each Step freezes its market window/due boundary, remaining quantity before
evaluation, relevant versions, and derived seed.

A Step may append zero or more `FillEvaluationAttempt` audit records. A pre-due
`PENDING_NO_ELIGIBLE_MARKET` attempt is not terminal and is never updated; the same
Step may later resolve when eligible evidence arrives. A Step is eventually claimed
by exactly one cross-variant terminal resolution: `MarketObservationSelection`,
`NoMarketOutcome`, `CancelledOutcome`, or `ExpiredOutcome`. Once claimed, its
resolution cannot be replaced.

Each Step's market selection is stored before fill calculation. It may select only
the exact Pair inside the frozen Step window, with local receipt no later than the
Step due boundary, local availability by evaluation, valid timestamps, and configured
freshness. Eligible observations are ordered by `received_at`, provider timestamp,
then observation ID, all ascending. Research Forward Results and future observations
are forbidden. Retry reuses that Step's selection; a later Step may select new
evidence inside its own frozen window.

One selection creates zero or one immutable `PaperFill`. A positive Fill cannot
exceed `remaining_quantity_before`. Remaining quantity is reconstructed from original
quantity and ordered persisted Fills. Only a positive partial fill with a positive
remainder may create the next contiguous Step; total fills cannot exceed the approved
quantity.

Under `fill-no-market-v1`, no eligible evidence before due creates only append-only
PENDING attempts. At/after due, absence of evidence locally received within the Step
window first-writes terminal `NoMarketOutcome`. Evidence discovered after that
terminal write cannot revise the Step, even if its receipt timestamp predates due.
Order terminal state and Step terminal resolution are distinct: `PARTIALLY_FILLED`
may continue, while `FILLED`, `CANCELLED`, `EXPIRED`, and `REJECTED` forbid new Steps.

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
- Paper Signal authorization remains compatible with existing SHADOW approvals while
  Paper authority remains explicit in operational lineage.
- Restart and backfill cannot change the meaning of an already claimed schedule slot;
  a later input belongs to a later slot.
- Deterministic fill replay also requires deterministic, frozen evidence selection;
  a deterministic formula over newly selected evidence is not historical identity.
- Partial-fill replay requires ordered Step and Fill lineage; an intent-level single
  selection cannot describe later evaluation of its remainder.
- PENDING remains append-only operational audit without falsely consuming terminal
  resolution authority for its Step.
- Fill quality and PnL remain attributable to exact model/evidence versions rather
  than being mistaken for Broker truth.
- A future Live rollout must create its own operator authority, limits, kill switch,
  reconciliation, rollback, and acceptance evidence in ExecPlan 0007.
- More schema and recovery work is required than returning a synthetic
  `OrderResult`, but the resulting evidence is reproducible and auditable.
