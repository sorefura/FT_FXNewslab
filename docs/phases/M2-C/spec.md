# M2-C Frozen Specification — Production Entry Strategy and Persistence

Status: frozen design input for the Phase Goal workflow.

This snapshot defines Milestone 2-C only. The living ExecPlan remains the place for
progress and decision-log updates; implementation must not edit this file after the
phase baseline is committed.

## Objective

Consume the Pair Signal materialized by M2-B5, authorize its exact content through
Live Adoption, evaluate it with a concrete deterministic
`NewsFilteredCarryStrategy`, and append lossless operational Swap, evaluation, and
optional Production Candidate evidence to the Live database.

The supported Pair order is exactly `USD_JPY`, then `MXN_JPY`. Thresholds, maximum
ages, Strategy versions, policy versions, and Swap source versions remain explicit
configuration or constructor inputs. M2-C introduces no production-value defaults.

## Non-goals

- M2-D Portfolio, Risk, ordinary close, quantity, or approved intent work.
- Paper execution, Broker integration, Private POST, or real-money authority.
- CycleSlot, scheduler, daemon, or CLI composition.
- An inferred external Swap API implementation.
- Research evaluation or a second Pair transformation formula.
- Changes to M2-B5 selection, SQL, checkpoint, transaction, or idempotency rules.
- Reuse of lossy legacy `live_candidates` for Production Candidate evidence.

## Cross-unit invariants

- Only exact `AuthorizedSignal` evidence reaches the Strategy.
- Strategy produces a typed evaluation and optional Candidate, never quantity,
  Portfolio/Risk decisions, an intent, or a Broker call.
- Pair transformation remains owned by the shared `currency-pair-v1` path.
- Unknown, unavailable, stale, malformed, missing, zero, or negative Swap is never
  interpreted as zero carry and never creates a Candidate.
- Persisted corruption is an integrity failure, not a normal business skip.
- Every polymorphic trust boundary revalidates exact concrete contract types by
  invoking the base validator, so subclass overrides cannot disable validation.
- Exact replay converges on persisted evidence; conflicting content with an existing
  semantic identity fails without partial rows.
- `SHADOW_NOT_SUBMITTED` and `PAPER` request Adoption `RuntimeMode.SHADOW`.
  `LIVE` is rejected before materialization, authorization, Swap access, Strategy,
  persistence, or Broker work.

## Frozen design decisions

### Invalid or absent Swap input

Provider input that cannot construct an intrinsically valid
`OperationalSwapEvidence` is not disguised as Strategy evidence. The acquisition
boundary returns an exact `OperationalSwapResolution` value. Its terminal outcome is
one of:

- `EVIDENCE`, containing one intrinsically valid exact-type
  `OperationalSwapEvidence`;
- `MISSING`, containing no Evidence; or
- `MALFORMED`, containing no Evidence.

Every resolution commits to Pair, acquisition source and source version, requested
UTC time, terminal outcome, and a stable reason code. Its content-addressed resolution
ID excludes exception text and raw provider payload. `EVIDENCE` additionally commits
to the exact Swap evidence ID and intrinsic content. `MISSING` and `MALFORMED` cannot
carry numeric amounts or an evidence ID. This is operational acquisition evidence,
not Strategy evidence.

A malformed persisted row is an integrity error.

The M2-C SQLite adapter provides content-addressed append-or-compare and exact-ID
read only. Latest/as-of input selection belongs to the M4 `CycleInputSnapshot` and is
not introduced here.

### Entry work item and Swap handoff

`ProductionEntryWorkItem` is an immutable one-Pair application input containing the
configured Pair, exact materialization Request and its caller-supplied M2-B5 stage
times, execution-authority mode, authorization time, evaluation time, and one exact
`OperationalSwapResolution`. The work-item identity commits to all of those semantic
inputs. It contains neither quantity nor an already-computed Strategy result.

All work items are validated before durable work. For every outcome, including
`MISSING` and `MALFORMED`, `resolution.pair` must equal the work item's configured
Pair. An `EVIDENCE` resolution must additionally hold Evidence for that same exact
Pair and must already be available to the caller; M2-C never queries for a latest
row. `MISSING` or `MALFORMED` terminates that Pair before Strategy and returns a
deterministic typed pre-evaluation result derived from the work item and resolution
IDs.

For `EVIDENCE`, the first and only semantic persistence point for that Swap input in
the application path is B4's transaction. B4 uses B3's connection-scoped
append-or-compare primitive inside the same `BEGIN IMMEDIATE` transaction as config,
evaluation, and optional Candidate. It inserts the exact Evidence or proves an
identical existing row before writing the evaluation. B5 does not perform a separate
pre-Strategy Swap write. Exact-ID read remains available for an external caller to
construct a work item, but it is not a selection rule and is not part of B5.

### Carry reason partition

For BUY, long received carry is required; for SELL, short received carry is required.
If the required leg is non-positive while the opposite leg is strictly positive, the
reason is `DIRECTION_CARRY_MISMATCH`. If neither leg supplies the required positive
carry, the reason is `CARRY_NOT_POSITIVE`.

### Production persistence

`ProductionTradeCandidate` is stored in new lossless Production tables. It is never
converted into the legacy `live_candidates.score REAL` representation, which cannot
preserve PairScore, confidence, Swap lineage, or approved configuration identity.

### Evaluated Pair and skip identity

M2-C adds `evaluated_pair: CurrencyPair` to
`ProductionEntryEvaluationInput`. It is mandatory, exact-typed, and included in the
entry evaluation identity. `ProductionEntryEvaluation.create_candidate()` and
`create_skip()` both use this field as the evaluation Pair; neither derives it from
Swap evidence. Candidate creation additionally requires the authorized Pair Signal
to match it. This lets B1 represent `SIGNAL_NOT_PAIR_TARGET` without depending on the
B5 work-item type and prevents a wrong-Pair Swap from changing the recorded Strategy
evaluation Pair.

## B1 — Concrete deterministic Strategy

Extend `ProductionEntryEvaluationInput` with the frozen `evaluated_pair` field, then
implement `NewsFilteredCarryStrategy(config)` and return exactly one
`ProductionEntryEvaluation` for one Pair input. Every result identity commits to that
explicit evaluated Pair.

Evaluation precedence is fixed:

1. Signal has a Pair target.
2. Pair is configured.
3. Signal type matches the configured type.
4. transformation version matches the configured version.
5. authorization Strategy ID and version match config.
6. approved config identity equals `config.strategy_config_identity`.
7. Signal is not in the future.
8. Signal is not stale.
9. direction crosses a threshold.
10. Swap Pair matches the evaluated Pair.
11. Swap availability is usable.
12. Swap receipt/effective window/freshness is usable.
13. required-side received carry is strictly positive.

Directional and time semantics:

- score strictly greater than the positive threshold is BUY;
- score strictly less than the negative threshold is SELL;
- equality with either threshold is neutral;
- future Signal means `signal.created_at > evaluated_at`;
- Signal age is measured from `signal.observed_at`, and only `age > signal_max_age`
  is stale;
- Swap received after `evaluated_at` is `SWAP_MALFORMED`;
- `effective_from` is inclusive, so only `evaluated_at < effective_from` is
  `SWAP_NOT_APPLICABLE`;
- `effective_until` is inclusive, so only `evaluated_at > effective_until`, or
  `received_at` age greater than `swap_max_age`, is
  `SWAP_STALE`;
- only the required received amount strictly greater than zero creates a Candidate.

Expected implementation surface:

- `apps/swap_bot/src/swap_bot/strategy/news_filtered_carry.py`
- `apps/swap_bot/src/swap_bot/strategy/contracts.py`
- `apps/swap_bot/src/swap_bot/strategy/__init__.py`
- `tests/strategy_contracts/test_news_filtered_carry.py`
- applicable architecture tests and living documentation

B1 evidence covers both Pairs, BUY and SELL, threshold and freshness equality,
every skip branch and precedence collision, deterministic identities, exact-type
validation, and prohibited imports.

## B2 — Materialized Pair Signal to Live authorization

Add a public bridge that reconstructs the exact Pair `Signal` from an authenticated
M2-B5 result and passes it to the Live Adoption Gate.

- Reauthenticate result, Completion, Selection Snapshot, Request, Specification,
  and Signal Snapshot with exact concrete types and base validators.
- Require config Pair to equal Request and Specification Pair.
- Admit only SELECTED artifacts with operational outcome `MATERIALIZED` or
  `REUSED_IDENTICAL`.
- `NO_SELECTION` and `AMBIGUOUS` create no authorization.
- Stop Pair, Signal type, transformation, Strategy, or config mismatch before the
  Adoption Gate.
- Reconstructed Signal semantic fields must equal the authenticated snapshot.
- Require `reconstructed_signal.created_at <= authorized_at` before calling the Live
  Adoption Gate. Equality is allowed; a backdated authorization is rejected.
- Reject `LIVE` before calling materialization or authorization.
- Do not change M2-B5 Claim, Selection, Completion, SQL, or transaction behavior.

Expected implementation surface:

- public reconstruction helper in `fx_signal_store`
- `apps/swap_bot/src/swap_bot/signals/materialized_pair.py`
- `apps/swap_bot/src/swap_bot/signals/__init__.py`
- `tests/pair_signal_materialization/test_live_authorization_bridge.py`
- applicable architecture tests and living documentation

## B3 — Exact operational Swap persistence adapter

Add Live migration `0003_operational_swap_evidence.sql` and a SQLite adapter with:

- append-or-compare by content-addressed evidence ID;
- exact-ID read returning typed `FOUND` or `MISSING`;
- lossless Decimal text, including signed zero;
- exact unit, settlement currency, source/version, provider/receipt time, and
  effective-window hydration;
- immutable UPDATE/DELETE guards;
- no latest/as-of query, database-natural ordering, or dynamic provider adapter.

Strengthen the Live migration runner so each migration body and its marker commit in
one writer transaction. Fresh database creation, upgrade from Live `0002`, reopen,
failure rollback/retry, and concurrent initialization must converge. A malformed
persisted row must not be returned as `MISSING`.

Expected implementation surface:

- `apps/swap_bot/src/swap_bot/operational_swap.py`
- `apps/swap_bot/src/swap_bot/migrations/0003_operational_swap_evidence.sql`
- `apps/swap_bot/src/swap_bot/live_migrations.py`
- `tests/strategy_persistence/test_operational_swap_store.py`
- applicable migration, architecture, and living-document tests

## B4 — Atomic evaluation and Candidate persistence

Add Live migration `0004_production_entry_strategy.sql` and an append-or-compare
store for exact config, evaluation, and optional Candidate evidence.

Logical roots:

- `live_news_filtered_carry_configs`
- `live_operational_swap_evidence` from B3
- `live_production_entry_evaluations`
- `live_production_trade_candidates`
- Pair materialization Request ID and Pair Signal content-hash lineage

The store must use a short `BEGIN IMMEDIATE` boundary and:

- accept only an `EVIDENCE` work-item resolution and append-or-compare its exact Swap
  evidence through B3's connection-scoped primitive in this same transaction;
- reconstruct persisted authorization, approval, evidence snapshot, and policy;
- use `evaluation_input.evaluated_at` as the frozen semantic authority instant for
  every authority recheck; transaction wall-clock time is never consulted;
- require
  `authorized_pair_signal.signal.created_at <= authorization.authorized_at <= evaluated_at`;
  both equalities are allowed and any backdated authorization is rejected;
- require Signal creation, authorization, and evaluation times to be at or after
  `max(approval.effective_from, approval.decided_at)`;
- treat approval expiry as exclusive: `evaluated_at >= expires_at` is rejected;
- reject a revocation exactly when its `decided_at <= evaluated_at`; a later
  revocation does not invalidate historical replay;
- recheck the approval's exact `strategy_config_identity`;
- reauthenticate materialization and Authorized Signal content equality;
- rerun the Strategy with the same input instead of trusting a caller-supplied
  result;
- store CANDIDATE with exactly one Candidate and SKIP with none;
- preserve PairScore and confidence losslessly;
- write nothing to legacy Candidate, Portfolio, or Risk tables;
- return inserted or `REUSED_IDENTICAL`, with one insert plus one reuse under
  concurrent identical writers;
- fail conflicting lineage or content without partial rows.

Expected implementation surface:

- `apps/swap_bot/src/swap_bot/production_strategy_store.py`
- `apps/swap_bot/src/swap_bot/migrations/0004_production_entry_strategy.sql`
- `tests/strategy_persistence/test_production_entry_store.py`
- applicable authorization, migration, architecture, and living-document tests

M2-D starts with the next available Live migration number.

## B5 — Ordered M2-C entry application composition

Add an application service that accepts the frozen `ProductionEntryWorkItem` contract
and prevalidates exactly one work item for each configured Pair in config order before
the first durable call. Each Pair then executes once:

```text
authority guard
-> Pair materialization
-> selected artifact authentication
-> Live Adoption authorization
-> authenticate the work item's exact Swap resolution
-> NewsFilteredCarryStrategy
-> atomic evaluation/Candidate persistence
```

Pair non-selection and Swap `MISSING`/`MALFORMED` return typed pre-evaluation results.
Only `EVIDENCE` reaches Strategy and B4; B4 atomically append-compares that exact Swap
with the evaluation and optional Candidate. Persisted Swap or Adoption corruption and
SQLite failure propagate as errors. The service adds no automatic retry, sleep, or
backoff.

Expected implementation surface:

- `apps/swap_bot/src/swap_bot/production_entry.py`
- `tests/production_entry_application/test_production_entry_service.py`
- applicable architecture and living-document tests

B5 must preserve both Pair results rather than silently collapsing two Candidates to
one. Replay must converge on M2-B5, authorization, Swap, evaluation, and Candidate
evidence. The M2-C root must not call Portfolio, Risk, Execution, Paper, or Broker.
