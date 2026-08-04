# M2-C Frozen Acceptance Criteria

Status: frozen acceptance input for the Phase Goal workflow.

M2-C is accepted only when all of the following are true.

## Unit gates

- B1, B2, B3, B4, and B5 completed in order.
- Every B unit received an approval from a newly created `phase_reviewer` identity.
- Every rejected attempt was corrected by the selected implementer and reviewed by
  another new reviewer identity.
- A new `phase_final_reviewer` approved the complete Phase after full checks.

## Functional evidence

- The concrete Strategy deterministically emits the same evaluation and optional
  Candidate for identical Authorized Signal, config, Swap evidence, and clock input.
- `USD_JPY` and `MXN_JPY` are both evaluated in frozen config order.
- BUY and SELL require strictly positive carry on the matching received leg.
- Threshold equality is neutral and freshness equality remains eligible.
- Swap `effective_from` and `effective_until` equality are eligible; only a time
  strictly outside the inclusive effective window is rejected for that window.
- Missing, malformed, future, stale, unavailable, not-applicable, wrong-Pair,
  non-positive, or direction-misaligned Swap never creates a Candidate.
- Pair non-selection and acquisition-level missing/malformed Swap have typed
  content-addressed pre-evaluation outcomes and are not represented by forged
  Strategy evidence.
- Persisted corruption is rejected as an integrity failure, not converted to a
  normal skip.
- `SHADOW_NOT_SUBMITTED` and `PAPER` authorize through `RuntimeMode.SHADOW`.
- `LIVE` reaches none of materialization, Adoption, Swap, Strategy, persistence, or
  Broker work.
- Signal creation, authorization, and evaluation obey
  `signal.created_at <= authorized_at <= evaluated_at`; equality is eligible and a
  backdated authorization is rejected both before the Gate and at persistence.

## Lineage and persistence evidence

- Exact Pair, config, materialization, Pair Signal, authorization, adoption approval,
  Swap, evaluation, and Candidate lineage can be reconstructed and revalidated.
- Every Pair starts from one immutable `ProductionEntryWorkItem` whose exact
  `OperationalSwapResolution` is `EVIDENCE`, `MISSING`, or `MALFORMED`; latest-row or
  database-natural selection is absent.
- Resolution Pair equals work-item configured Pair for `EVIDENCE`, `MISSING`, and
  `MALFORMED`, and mismatch fails before durable work.
- `ProductionEntryEvaluationInput.evaluated_pair` is mandatory, exact-typed, included
  in evaluation identity, and remains the recorded Pair even for non-Pair Signals or
  wrong-Pair Swap evidence.
- For `EVIDENCE`, B4 append-compares the exact Swap row in the same transaction as
  config, evaluation, and optional Candidate; there is no earlier B5 Swap write.
- `evaluation_input.evaluated_at` is the authority recheck instant. Authority start
  is inclusive, expiry is exclusive, revocation at the same instant is effective,
  and a later expiry/revocation does not break exact historical replay.
- Candidate PairScore and confidence are lossless and remain separate.
- CANDIDATE has exactly one Production Candidate; SKIP has none.
- Exact retry returns identical semantic evidence and converges on reuse.
- Concurrent identical writers converge on one insert plus reuse.
- Identity/content conflict, forged nested evidence, authorization/config mismatch,
  or failed transaction leaves no partial semantic rows.
- Operational Swap and Production Strategy tables are immutable and append-oriented.
- Migration body and marker are atomic across fresh creation, Live `0002` upgrade,
  reopen, injected failure/retry, and concurrent initialization.
- Live migrations end at `0004_production_entry_strategy.sql`; Signal Store migrations
  remain unchanged at `0001` through `0004`.
- Legacy `live_candidates`, Portfolio, and Risk tables receive no M2-C writes.

## Scope and architecture evidence

- M2-B5 behavior and its existing regression suite remain unchanged.
- Strategy imports no AI provider, Research evaluator, Portfolio, Risk, Execution,
  Paper, Broker, or Private transport.
- The M2-C application root calls no Portfolio, Risk, Execution, Paper, or Broker
  component.
- M2-D, Paper persistence, Broker integration, CycleSlot, scheduler, daemon, CLI,
  dynamic external Swap provider, and production config defaults are absent.
- Living architecture, Swap Bot, data/versioning, repository-structure, test-strategy,
  README, and ExecPlan M2-C progress documentation match the implementation.

## Required checks

- `python -m pytest -q` succeeds.
- `python -m ruff check .` succeeds.
- strict mypy succeeds for `packages/fx_core/src`,
  `packages/fx_signal_store/src`, `apps/fx_research/src`, and `apps/swap_bot/src`.
- `git diff --check` succeeds.
- Gate state verifies the complete per-unit reviewer history before final review.
- Final review bundle contains the frozen files, full diff, check evidence/log hashes,
  and review request defined by the workflow reviewer contract; the final verdict is
  bound to that bundle's unique nonce.
