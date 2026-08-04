# M2-D Frozen Specification — Ordinary Close Portfolio/Risk Path

Status: frozen design input for the Phase Goal workflow.

This snapshot defines Milestone 2-D only. The living ExecPlan remains the place for
progress and decision-log updates; implementation must not edit this file after the
phase baseline is committed.

## Objective

Turn one exact ordinary Position-exit work item into either durable KEEP evidence or
the separate typed chain below:

```text
exact operational exit evidence
-> ProductionPositionExitEvaluation
-> PositionCloseCandidate (quantity-free)
-> OrdinaryClosePortfolioDecision (quantity allocation)
-> OrdinaryCloseRiskDecision (reduce-only/no-overclose proof)
-> ApprovedCloseIntent (evidence only; no execution)
```

M2-D completes the production Strategy ordinary-exit path while retaining the
accepted M2-A/M2-C contracts and the separate Risk emergency liquidation authority.
All new identities are content-addressed and all new persistence is additive and
append-oriented.

## Non-goals

- Paper, Broker, order submission, fill, cancellation, reservation release, or
  Position/account ledger implementation.
- Scheduler, daemon, CLI, CycleSlot, batch Position selection, or latest-row lookup.
- Changes to entry Strategy, Pair materialization, Adoption, or operational Swap
  selection/persistence behavior.
- Mutation or reinterpretation of accepted `PositionCloseCandidate`,
  `ProductionPositionExitEvaluation`, `NewsFilteredCarryStrategyConfig`, legacy
  `TradeCandidate`, `PortfolioDecision`, `RiskDecision`, `ApprovedExecutionIntent`,
  or `ApprovedLiquidationIntent` identities.
- Writes to legacy `live_candidates`, `live_portfolio_decisions`,
  `live_risk_decisions`, or `live_execution_intents`.
- Any inheritance, union, action-string, or conversion relationship between ordinary
  close and emergency liquidation.
- Migrations below Live `0005` or Signal Store migration changes.
- Broker lot-size rounding, minimum order size, or production-value defaults.

## Cross-unit invariants

- Strategy remains quantity-free. Portfolio is the only quantity-allocation owner;
  Risk may reject but never enlarge or replace the Portfolio quantity.
- Accepted M2-A exit objects remain unchanged. Additive operational work/result
  envelopes bind the typed acquisition outcomes and quantity evidence that those
  accepted objects do not intrinsically contain.
- One exact capacity identity binds Position ID, Position evidence ID, Pair, existing
  Side, observation time, quantity unit, and positive finite Decimal open quantity.
- Quantity unit is exact base-currency units. M2-D performs no rounding. An explicit,
  versioned allocation policy supplies a target fraction in `(0, 1]`; no default is
  introduced.
- Close Side is always opposite the existing Position Side. Every approved quantity
  is positive and no greater than the capacity available before that decision.
- Existing M2-D `ApprovedCloseIntent` rows for the same business `PositionId` reserve
  their full quantities across later capacity observations. Until M3 adds typed
  fill/release evidence, reservations are never released or reduced. A new capacity
  ID alone cannot reset reservations.
- For one Position ID:

  ```text
  sum(all approved ordinary-close reservations) <= observed open quantity
  ```

- Exact replay returns the original semantic result before considering later
  reservations. Distinct concurrent requests are serialized by the database writer
  boundary and can never over-reserve.
- KEEP has no Candidate, Portfolio decision, Risk decision, or Intent. CLOSE has
  exactly one Candidate and one Portfolio decision; it also has one Risk decision.
  Only Risk APPROVE has exactly one `ApprovedCloseIntent`.
- Every external/store boundary requires exact concrete runtime types and calls the
  base validator. Subclass validator overrides, missing fields, and comparison-
  overriding string subclasses fail closed before outcome routing or writes.
- Persisted corruption is an integrity failure, never a business KEEP/REJECT, reuse,
  repair, or retry signal.
- Decimal values are persisted as lossless text. SQLite `REAL` or host floating-point
  arithmetic must not prove quantity or no-overclose.
- `SHADOW_NOT_SUBMITTED` and `PAPER` are allowed intent authorities. `LIVE` is
  rejected during full work-item prevalidation, before Strategy or durable work.
- No automatic retry, sleep, backoff, Broker call, or Execution call is added.

## Frozen operational evidence model

### Additive work and result envelopes

`OrdinaryPositionExitWorkItem` is a content-addressed one-Position input. It contains:

- the accepted exact `ProductionPositionExitEvaluationInput`;
- one exact `PositionCloseCapacityEvidence`;
- one exact typed Signal/Adoption terminal resolution;
- one exact `OperationalSwapResolution`;
- one explicit `OrdinaryCloseAllocationPolicy`;
- one explicit `OrdinaryCloseRiskPolicy`;
- `ExecutionAuthorityMode`; and
- no caller-computed evaluation, decision, quantity, Intent ID, idempotency key, or
  transaction time.

The work item validates the complete relation before any durable work. Capacity and
accepted Position evidence must have identical Position ID, Position evidence ID,
Pair, existing Side, and observed time. Swap resolution Pair and requested/terminal
evidence must match the Position Pair and checkpoint lineage. All semantic times are
UTC and cannot be in the future relative to `evaluated_at`.

The accepted exit evaluation cannot intrinsically commit to the new operational
resolution types without changing its accepted identity. Therefore
`OperationalPositionExitEvaluationResult` is an additive content-addressed root that
commits to the exact work-item ID and the nested accepted evaluation identity/content.
Persistence and downstream decisions reference this operational root as well as the
accepted Candidate.

### Signal and Adoption terminal resolution

Add one exact typed resolution with these terminal outcomes:

- `AUTHORIZED`: contains one exact `AuthorizedSignal` matching the accepted input;
- `NO_SELECTION`: contains no Signal/authorization;
- `AMBIGUOUS`: contains no Signal/authorization; or
- `ADOPTION_INACTIVE`: contains no current authorization.

The resolution commits to the M2-B Request/Claim/Selection/Completion identities
when selection was attempted, the exact adoption evidence/decision identity when
authorization was attempted, its terminal reason code, and its resolved-at time.
Opaque context IDs alone never prove a terminal outcome. The resolution and accepted
input/context IDs must match exactly.

### Position capacity

`PositionCloseCapacityEvidence` is a new immutable versioned contract. Its identity
commits to:

- `position_id` and the exact accepted `position_evidence_id`;
- Pair and existing Side;
- `position_observed_at` equal to the accepted Position observation;
- positive finite Decimal `open_quantity`;
- exact quantity unit `BASE_UNITS`; and
- a versioned capacity-source/checkpoint identity.

This evidence does not modify `PositionExitPositionEvidence` and is not a mutable
Position ledger. A work item with zero/negative, non-finite, wrong-unit, wrong-
Position, wrong-Pair, wrong-Side, or mismatched-time capacity fails before writes.

### Allocation and risk policies

`OrdinaryCloseAllocationPolicy` has an exact policy version and Decimal
`target_fraction` in `(0, 1]`. Portfolio computes:

```text
target_quantity = open_quantity * target_fraction
available_before = open_quantity - sum(prior approved reservations)
allocated_quantity = min(target_quantity, available_before)
```

No quantization is performed. `available_before <= 0` is Portfolio REJECT;
`0 < available_before < target_quantity` is REDUCE; otherwise it is ACCEPT.
If authenticated prior outstanding reservations already exceed the newly supplied
open quantity, the state is inconsistent and fails as an integrity error before a
Portfolio business decision. Equality is the valid zero-capacity REJECT case.

`OrdinaryCloseRiskPolicy` has an exact policy version and a positive maximum capacity
age. Risk rejects capacity from the future or with age strictly greater than the
configured maximum. Equality remains eligible. The policy cannot authorize opening,
same-Side, or quantity-increasing behavior.

## Frozen exit evaluation semantics

The operational evaluator produces the already-accepted
`ProductionPositionExitEvaluation` plus its additive operational root. It does not
trust a caller-provided evaluation.

After intrinsic and relational validation, trigger precedence is fixed:

1. `ADOPTION_INACTIVE` -> `ADOPTION_NO_LONGER_ACTIVE`.
2. Signal is absent/ambiguous, or an authorized Signal is stale, and
   `close_on_missing_or_stale_signal` is true ->
   `REQUIRED_SIGNAL_MISSING_OR_STALE`.
3. A current authorized Pair Signal strictly crosses the opposite configured entry
   threshold and `close_on_signal_reversal` is true -> `SIGNAL_REVERSED`.
4. Swap is missing/malformed/unusable/stale and
   `close_on_missing_or_stale_swap` is true ->
   `REQUIRED_SWAP_MISSING_OR_STALE`.
5. The received-carry leg for the existing Side is non-positive and
   `close_on_non_positive_carry` is true -> `CARRY_NO_LONGER_POSITIVE`.
6. `maximum_holding_age` is configured and holding age is greater than or equal to
   it -> `MAXIMUM_HOLDING_AGE`.
7. Otherwise -> KEEP with `NO_EXIT_CONDITION`.

An existing BUY reverses only when PairScore is strictly below the negative entry
threshold; an existing SELL reverses only when PairScore is strictly above the
positive threshold. Threshold equality is not reversal. Signal/Swap freshness uses
strict `age > maximum`; equality is current. Swap effective-window endpoints are
inclusive. A future Signal, authorization, resolution, Swap receipt, or Position
observation is invalid evidence rather than a business KEEP.

If a configured close flag is false, that condition does not terminate evaluation;
the evaluator continues to later rules. Adoption inactivity is an unconditional
safety close. Unsupported Signal target/type/transformation/Strategy/config lineage
is an integrity error, not missing evidence.

## B1 — Operational exit evidence and deterministic evaluator

Add exact typed Signal/Adoption resolution, capacity evidence, one-Position work item,
operational evaluation result, and deterministic exit evaluator. Preserve accepted
M2-A identities and emit the accepted KEEP/CLOSE evaluation nested in the additive
root. Cover every terminal outcome, precedence collision, flag combination, both
Pairs, both existing Sides, time/threshold equality, deterministic IDs, and exact-
type adversarial inputs.

Expected implementation surface includes new ordinary-close domain/application
modules, additive public exports, focused Strategy tests, architecture tests, and
living documentation. B1 adds no migration or store.

## B2 — Close-specific Portfolio and Risk contracts

Add distinct immutable versioned contracts for:

- ordered `OrdinaryCloseReservationSnapshot` containing every exact prior approved
  and not-yet-released Intent ID/quantity for one Position ID, including Intents
  created from an older capacity observation;
- `OrdinaryClosePortfolioDecision` with Candidate/operational-evaluation/capacity/
  policy/reservation lineage, target, available-before, disposition, and optional
  allocated quantity;
- `OrdinaryCloseRiskDecision` with the exact Portfolio chain, risk policy, outcome,
  and structured reason; and
- `ApprovedCloseIntent` with exact Candidate, Portfolio, Risk, capacity, Position,
  Pair, opposite Side, quantity, authority, deterministic idempotency key, and
  creation time.

The close-specific services operate only on these exact types. Portfolio selects the
quantity using the frozen formula. Risk revalidates all lineage, freshness,
opposite-Side behavior, positive quantity, and the no-overclose equation. Risk never
changes quantity. Rejected Portfolio is represented by a linked Risk REJECT; only an
APPROVE decision can construct one exact ordinary-close Intent.

B2 must prove that no close type subclasses, wraps as a union, writes as, or can be
passed as `ApprovedLiquidationIntent` or any entry-path type.

## B3 — Exit evaluation persistence

Add `0005_ordinary_close_path.sql` and append-or-compare tables for operational work
evidence, capacity evidence, typed resolution evidence, operational exit evaluation,
the nested accepted exit evaluation, and optional Position close Candidate.

One short `BEGIN IMMEDIATE` transaction must:

- authenticate every exact work-item root and its persisted M2-B/Adoption/Swap/config
  parents;
- rerun the deterministic evaluator rather than trust caller output;
- append-or-compare exact work, capacity, resolution, evaluation, and Candidate;
- require KEEP to have zero Candidates and CLOSE to have exactly one; and
- return `INSERTED` or `REUSED_IDENTICAL` with no partial rows on conflict/failure.

Identical concurrent writers converge on one insert plus reuse. New tables have
immutable UPDATE/DELETE guards. Migration body and marker are atomic for fresh
creation, upgrade from `0004`, reopen, failure/retry, and concurrent initialization.

## B4 — Atomic Portfolio/Risk decision and capacity reservation

For one persisted CLOSE result, use one short `BEGIN IMMEDIATE` transaction. First
return an exact already-persisted semantic chain for the same Candidate; this makes
replay independent of later reservations. Otherwise:

1. hydrate and authenticate the exact operational evaluation, Candidate, capacity,
   policies, and authority;
2. read and authenticate every prior `ApprovedCloseIntent` for that same business
   Position ID in deterministic persisted order, regardless of originating capacity
   identity;
3. construct the exact reservation snapshot and Portfolio decision;
4. construct and validate the Risk decision;
5. append both decisions and, only for APPROVE, exactly one Intent; and
6. re-read and validate the complete result before commit.

The Candidate-to-Portfolio relation is unique. Distinct writers may be ordered by
first database writer, but each decision commits to its exact prior reservation
snapshot. Two distinct concurrent requests cannot reserve more than capacity.
Conflict, corruption, missing parent, or injected failure rolls back the complete B4
transaction and is never repaired.

## B5 — One-Position ordinary-close application composition

Add a one-Position application service. It fully prevalidates the exact work item and
rejects `LIVE` before Strategy or durable work, then executes once:

```text
B1 operational evaluation
-> B3 atomic evaluation persistence
-> KEEP: typed terminal result
-> CLOSE: B4 atomic Portfolio/Risk/reservation
-> typed terminal result containing zero or one ApprovedCloseIntent
```

The application preserves structured KEEP, Portfolio REJECT/REDUCE/ACCEPT, Risk
REJECT/APPROVE, and persistence outcomes. It performs no Position discovery or batch
selection; M4 owns cycle input freezing. It adds no automatic retry, sleep, Execution,
Paper, Broker, Private transport, or emergency liquidation call. Manual exact replay
must converge through B3 and B4 and return the original semantic chain.
