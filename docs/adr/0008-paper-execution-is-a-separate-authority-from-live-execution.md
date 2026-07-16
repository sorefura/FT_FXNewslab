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

One approved intent first-writes one immutable `FillEvaluationPlan`, fixing its due
boundary, fill/selection/spread/slippage/liquidity/partial-fill versions, and seed.
Before fill calculation, one immutable `MarketObservationSelection` is stored. It may
select only the exact Pair with local
`intent.created_at <= received_at <= fill_due_at`, local availability by evaluation,
valid timestamps, and configured freshness. Eligible observations are ordered by
`received_at`, provider timestamp, then observation ID, all ascending. Research
Forward Results and future observations are forbidden.

Under `fill-no-market-v1`, absence of eligible evidence is `PENDING` before the due
boundary and terminal `REJECTED_NO_MARKET_EVIDENCE` at or after it. Retry reuses the
stored selection or terminal outcome, and cannot move the boundary, choose a new
seed, or fill from a newer quote.

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
- Fill quality and PnL remain attributable to exact model/evidence versions rather
  than being mistaken for Broker truth.
- A future Live rollout must create its own operator authority, limits, kill switch,
  reconciliation, rollback, and acceptance evidence in ExecPlan 0007.
- More schema and recovery work is required than returning a synthetic
  `OrderResult`, but the resulting evidence is reproducible and auditable.
