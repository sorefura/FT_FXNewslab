# M2-D Frozen Acceptance Criteria

Status: frozen acceptance input for the Phase Goal workflow.

M2-D is accepted only when all of the following are true.

## Unit gates

- B1, B2, B3, B4, and B5 completed in order.
- Every B unit is approved by a newly created `phase_reviewer` identity.
- Every rejection is corrected and reviewed by another new reviewer identity.
- A new `phase_final_reviewer` approves the complete Phase after full checks.

## Exit evaluation evidence

- The additive work/evaluation roots preserve accepted M2-A contract identities while
  committing to exact typed acquisition outcomes and capacity evidence.
- Inactive Adoption, missing/ambiguous/stale Signal, strict opposite-threshold
  reversal, unusable/stale/non-positive carry, maximum holding age, and KEEP follow
  the frozen precedence and flag behavior.
- Threshold equality is not reversal; Signal/Swap maximum-age equality and Swap
  effective-window equality remain eligible; holding-age equality closes.
- Both supported Pairs and both existing Position Sides produce deterministic IDs.
- Unsupported or forged Position/Pair/Side/config/Signal/authorization/Swap/
  checkpoint lineage, future evidence, nested subclasses, missing fields, and
  comparison-overriding strings fail before writes.
- KEEP persists exactly zero Candidates and CLOSE exactly one accepted
  `PositionCloseCandidate` nested under one operational evaluation root.

## Quantity, Portfolio, and Risk evidence

- Capacity binds exact Position ID, Position evidence ID, Pair, existing Side,
  observation time, source/checkpoint, `BASE_UNITS`, and positive finite Decimal
  quantity without modifying Strategy evidence.
- Target fraction is explicit, versioned, Decimal, and in `(0, 1]`; no default,
  float arithmetic, rounding, lot-size, or minimum-order behavior is introduced.
- Full allocation, policy partial allocation, reduction to remaining capacity, and
  zero-capacity rejection follow the frozen formula losslessly.
- Prior outstanding reservations greater than current observed open quantity are an
  integrity failure, not a Portfolio REJECT; equality is zero-capacity REJECT.
- Every approved Side is opposite the existing Position Side and every approved
  quantity is positive and no greater than `available_before`.
- Risk validates the exact Candidate/operational evaluation/Portfolio/capacity/
  policy/reservation chain, rejects stale/future capacity, and never changes quantity.
- Rejected Portfolio has one linked Risk REJECT and no Intent. Risk APPROVE has
  exactly one `ApprovedCloseIntent`; every other state has none.
- `ApprovedCloseIntent` has deterministic identity/idempotency and explicit
  `SHADOW_NOT_SUBMITTED` or `PAPER` authority. `LIVE` fails before any Strategy/store
  call.
- Ordinary close cannot be accepted as emergency liquidation or an entry decision/
  intent, and emergency liquidation code and tests remain unchanged.

## Persistence and concurrency evidence

- Live migration `0005_ordinary_close_path.sql` is additive, immutable, and does not
  alter migrations `0001` through `0004` or Signal Store migrations.
- Fresh database, `0004` upgrade, reopen, body/marker failure rollback and retry, and
  concurrent initialization converge through exactly Live `0005`.
- Work, resolution, capacity, evaluation, Candidate, reservation snapshot, Portfolio,
  Risk, and Intent evidence are append-or-compare and losslessly hydrated.
- Exact retry returns the original semantic result even after later reservations.
- Identical concurrent requests converge on one insert plus reuse.
- Distinct concurrent full-close requests for one Position ID, including different
  capacity evidence IDs, never make total
  approved quantity exceed observed open quantity.
- A later capacity observation does not release or hide an earlier M2-D reservation;
  only future typed M3 fill/release evidence may change outstanding reservation state.
- Every Portfolio decision commits to the exact ordered prior-reservation snapshot
  used to calculate `available_before`.
- Corrupt/missing parent, Candidate, reservation, decision, or Intent is an integrity
  error, is not repaired or reclassified, and leaves no partial rows.
- Injected failure at every B3/B4 write boundary rolls back that entire transaction.
- No new writes occur in legacy entry Candidate/Portfolio/Risk/Execution tables.

## Composition and scope evidence

- One exact work item is prevalidated before durable work; B3 runs once and B4 runs
  only for CLOSE.
- Application results preserve KEEP and every Portfolio/Risk terminal outcome.
- Manual replay converges; no automatic retry, sleep, or backoff is present.
- No Position discovery/latest-row selection, multi-Position batch/cycle freezing,
  Paper, Broker, order, fill, cancellation, reservation release, scheduler, daemon,
  CLI, Private transport, or real-money behavior is added.
- Strategy imports no Portfolio, Risk, Execution, Paper, Broker, Research evaluator,
  or AI provider dependency.
- Architecture/import tripwires prove no ordinary-close call reaches emergency
  liquidation, Execution, Paper, Broker, or Private transport.
- Living architecture, Swap Bot, data/versioning, repository-structure, test-strategy,
  README, and ExecPlan documentation match the implementation.

## Required checks

- `python -m pytest -q` succeeds.
- `python -m ruff check .` succeeds.
- strict mypy succeeds for `packages/fx_core/src`, `packages/fx_signal_store/src`,
  `apps/fx_research/src`, and `apps/swap_bot/src`.
- `git diff --check` succeeds.
- Repository text remains UTF-8 without BOM and all checks run from the Japanese
  Windows repository path.
- Gate state proves the complete per-unit reviewer history before final review.
- Final review uses only the immutable bundle, frozen files, full diff, test logs and
  hashes, and a unique reviewer nonce.
