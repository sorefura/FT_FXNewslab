# ExecPlan 0006: Production Strategy and Paper Trading Operations

This document is a living ExecPlan. Maintain `Progress`, `Surprises & Discoveries`,
`Decision Log`, and `Validation` as implementation proceeds. This plan follows
`PLANS.md`.

## Program context

FXNewslab separates discovery, description, choice, allocation, permission, and
execution:

```text
Research discovers.
Signal describes.
Strategy chooses.
Portfolio allocates.
Risk permits.
Execution performs.
```

The Program is divided into independently reviewable plans:

1. Shared Domain Foundation and Live Boundary Migration
2. Operational News Ingestion and Feature Production
3. Forward Signal Evaluation
4. Signal Validation Framework
5. Validated Signal Live Adoption
6. Production Strategy and Paper Trading Operations
7. Controlled Live Execution Rollout

ExecPlan 0005 grants an exact, bounded, revocable Signal permission to cross into a
named Strategy. It deliberately stops at `NOT_SUBMITTED`. This plan adds production
Strategy semantics and an operationally realistic simulated execution authority. It
does not add real order authority.

ExecPlan 0007 alone may introduce limited real Broker execution, canary limits, a
real-order kill switch, reconciliation, emergency stop, rollback, and Live
operational acceptance.

```text
VALIDATED_FOR_RESEARCH
    != APPROVED_FOR_STRATEGY
    != ORDER_APPROVED

SHADOW_NOT_SUBMITTED
    != PAPER_EXECUTED
    != LIVE_EXECUTED
```

Paper success, burn-in duration, simulated PnL, or operator confidence never grants
Live authority.

## Goal

Use real operational Signals, public market/swap observations, and an injected real
clock to run an approved production Strategy continuously and reproducibly through
Portfolio and Risk into fictional orders, fills, positions, account state, PnL, and
swap accrual. The completed path must survive restart and retry, reconcile its own
append-only evidence, and produce burn-in evidence while making real Broker Private
POST structurally unreachable.

The initial production Strategy is `NewsFilteredCarryStrategy`. It consumes an
approved persisted Pair Signal derived from currency-first fundamental evidence by
the existing versioned transformation contract, then emits an entry Candidate only
when directional conviction and positive received carry agree.

## Non-goals

- Real Broker orders, GMO Private POST, or any other external order submission.
- Enabling, weakening, or repurposing `LiveArmPolicy` or the two-step Live arming
  policy.
- ExecPlan 0007 Live rollout, canary sizing, real-order reconciliation, emergency
  stop, or rollback implementation.
- Automatic Research validation, automatic Strategy adoption, or automatic Live
  promotion.
- Signal, Forward Result, Evaluation, or validation formula changes.
- Research metric changes or use of Research `ForwardResult` as fill evidence.
- Portfolio/Risk/Execution rule redesign unrelated to supporting the separate paper
  boundary.
- Strategy parameter optimization, multi-strategy allocation, UI, RBAC, HA, or a
  generic backtesting framework.
- Implementing the Strategy, paper fill engine, ledger, or scheduler in this planning
  milestone.

## Current state

Planning started from clean, synchronized `main` at
`513e2632421062b6fefdb59f78ceab3979844dac`.

| Area | Current implementation | Gap carried into this plan |
|---|---|---|
| Strategy Port | `Strategy.evaluate(Sequence[AuthorizedSignal]) -> TradeCandidate | None` in `swap_bot.ports` | No production implementation, operational context, structured skip result, or multi-Candidate result. Tests use a `RecordingStrategy`; the offline fixture constructs a Candidate directly. |
| Candidate | Frozen `TradeCandidate` contains ID, Strategy ID/version, Pair, BUY/SELL side, `Probability` score, Signal IDs, and UTC creation time. | It is entry-only. `Probability` cannot losslessly store the existing `PairScore` range `[-2, 2]`; no silent clamp or unversioned normalization is allowed. |
| Entry/exit | Risk can create `ApprovedExecutionIntent`. `ApprovedLiquidationIntent` exists only for margin kill-switch liquidation. | There is no Strategy close Candidate, ordinary approved close intent, partial-close/reduce-only contract, exit reason, or Execution/Gateway close path. Emergency liquidation must not be reused as ordinary Strategy exit. |
| Swap | `swap_bot.swap` owns `SwapQuote`, `SwapAvailability`, `SwapDataSource`, `ManualOverrideSwapSource`, and priority/freshness selection. | Quote values lack unit basis, settlement currency, source version/content identity, rollover applicability, and accrual version. No dynamic operational adapter exists. |
| Position/account | `PositionSnapshot` has Pair, side, quantity, current price, and observation time. `AccountSnapshot` has only margin ratio and observation time. | No cash, realized/unrealized PnL, accrued swap, equity, used/available margin, gross exposure, open order, lot, or ledger contract. |
| Broker boundary | `BrokerGateway.submit(ApprovedExecutionIntent) -> OrderResult`. `GmoPrivatePostTransport.post_once` requires configuration plus `LIVE_TRADING_ARMED=YES` and does not retry. | No Paper Gateway exists. The low-level Private transport is not a complete Broker adapter and must remain outside paper composition. |
| Execution | `ExecutionService` accepts only `ApprovedExecutionIntent`, persistently claims its key, and always returns `NOT_SUBMITTED`; it never calls its injected Broker Gateway. | A boolean dry-run cannot represent fictional execution. Paper needs a distinct adapter, domain, result status, and authority. |
| Idempotency | Execution intent carries a caller-supplied string. SQLite has a unique intent key and a separate claimed-key table. | There is no canonical operational cycle identity or deterministic Paper order/fill identity. |
| Persistence | Live base tables are initialized inline. Numbered additive migrations `0001` and `0002` add adoption and Candidate-authorization state. | The next additive Live migration is `0003`; paper evidence and cycle recovery have no schema. |
| Signal source | `fx_signal_store` can read immutable Signals by ID or list by target/horizon/scorer version. Adoption runtime consumes a supplied Signal and never reads Research evaluation state. | There is no Live-owned operational Signal-source Port, checkpoint, ambiguity rule, or recurring selection cycle. |
| Pair transformation | `fx_core.CurrencyPairSignalTransformer` persists `currency-pair-v1` semantics: base direction minus quote direction. | The offline fixture uses it, but no operational producer selects matching base/quote currency Signals and stores the derived Pair Signal before Live authorization. |
| Modes | Adoption owns `SHADOW_ONLY`/`LIVE_ELIGIBLE`; its runtime gate currently has `SHADOW`/`LIVE`. | There is no execution-authority vocabulary distinguishing `SHADOW_NOT_SUBMITTED`, `PAPER`, and `LIVE`. |
| Operations | CLI supports one offline fixture cycle and one-shot approve/revoke commands. | No production one-shot cycle, scheduler/daemon, overlap lock, checkpoint, health signal, restart recovery, reconciliation, or burn-in report exists. |
| Pair/config values | There is no production settings module. `USD_JPY` appears in recorded fixtures and Research forward defaults. `MXN_JPY` is not configured. Safety numbers occur only in tests/fixtures. | Initial production scope is exactly `USD_JPY` and `MXN_JPY`, but neither fixture values nor Research defaults become production settings implicitly. All thresholds, ages, limits, and pair lists require immutable versioned config. |

The existing fixture values (`USD=10000`, `JPY=2000000`, margin ratio `1.0`, one
position per Pair, and 60-second account age) are test evidence, not accepted
production defaults.

## Target architecture

This is the target for ExecPlan 0006; none of the new Paper components shown below is
implemented by the planning commit.

```text
Operational Signal Source (shared immutable Signal store)
        |
        | exact persisted Pair Signal produced by currency-pair-v1
        v
Live Adoption Gate
        v
AuthorizedSignal
        v
NewsFilteredCarryStrategy <--- versioned SwapQuote evidence
        v
TradeCandidate or structured skip
        v
Portfolio -> Risk -> ApprovedExecutionIntent
        v
ExecutionAuthorityMode
        |-- SHADOW_NOT_SUBMITTED -> immutable NOT_SUBMITTED evidence
        |-- PAPER ----------------> PaperExecutionGateway
        |                              |
        |                         PaperOrder events
        |                              v
        |                         PaperFill evidence
        |                              v
        |                   Paper position/account ledger
        |                              v
        |                       PnL + swap accrual
        |
        `-- LIVE -> rejected throughout ExecPlan 0006
```

The close path is a separate branch, not an action string added to
`TradeCandidate`:

```text
Strategy PositionCloseCandidate -> Portfolio -> Risk -> ApprovedCloseIntent
Risk emergency decision ----------------------> ApprovedLiquidationIntent
                                                  |
                                                  v
                                      PaperExecutionGateway only
```

The exact close type names are finalized at the beginning of Milestone 2, before
schema implementation. Ordinary Strategy exit and Risk emergency liquidation remain
different reasons and authority chains. Both are reduce-only, can represent partial
close, and cannot increase exposure.

### Dependency direction

```text
fx_core <- fx_signal_store
   ^            ^
   |            |
swap_bot domain/application <- swap_bot paper infrastructure

paper infrastructure -/-> real Broker transport
swap_bot -/-> fx_research
```

Operational Live code may reuse the shared `CurrencyPairSignalTransformer` and
`fx_signal_store`. It may not import Research Forward observation, evaluation, or
provider adapters. A Live-owned public market-data Port/adapter supplies Paper quote
evidence.

## Public contracts and invariants

### Execution authority

Introduce a Live-owned explicit enum with at least:

```text
SHADOW_NOT_SUBMITTED
PAPER
LIVE
```

This is not a `dry_run: bool`. `SHADOW_NOT_SUBMITTED` creates no Paper order.
`PAPER` may create only Paper records through `PaperExecutionGateway`. `LIVE` is
rejected by configuration validation and the ExecPlan 0006 composition root before
any cycle starts. ExecPlan 0007 must add a new explicit rollout decision before a
real adapter can be composed.

The current adoption `RuntimeMode` is a distinct Strategy-input authorization
concept. The mapping is fixed as follows:

```text
ExecutionAuthorityMode.SHADOW_NOT_SUBMITTED -> Adoption RuntimeMode.SHADOW
ExecutionAuthorityMode.PAPER                -> Adoption RuntimeMode.SHADOW
ExecutionAuthorityMode.LIVE                 -> Adoption RuntimeMode.LIVE
```

`PAPER` is fictional execution, so it must not request Adoption `RuntimeMode.LIVE`.
A `SHADOW_ONLY` approval is usable by `SHADOW_NOT_SUBMITTED` and `PAPER` cycles. A
`LIVE_ELIGIBLE` approval is also usable by those modes, but does not grant real
Broker authority. `LIVE` mapping and its additional rollout authority belong only to
ExecPlan 0007; ExecPlan 0006 rejects `ExecutionAuthorityMode.LIVE` before Signal
authorization or cycle claim.

The two enums remain distinct. Adoption `RuntimeMode.SHADOW` is not
`ExecutionAuthorityMode.PAPER`, and Adoption `RuntimeMode.LIVE` is not approval to
execute against a Live Broker. A Paper cycle authorizes its Signals with
`RuntimeMode.SHADOW` and separately persists `ExecutionAuthorityMode.PAPER` in cycle,
order, and fill lineage. This plan adds neither `RuntimeMode.PAPER` nor a migration or
reinterpretation of existing adoption rows.

### Production Strategy

`NewsFilteredCarryStrategyConfig` is frozen, canonical, content-addressed, and at
minimum contains:

- Strategy ID and Strategy version;
- exact config identity;
- eligible Pairs, initially exactly `USD_JPY` and `MXN_JPY`;
- expected Pair transformation version;
- positive and negative entry thresholds;
- neutral-band semantics;
- Signal and swap freshness limits; and
- exit-policy version and its explicit parameters.

No default pair, threshold, freshness duration, or exit rule is hidden in code. A
configuration version cannot be reused with different content.

The target entry contract evaluates one Pair at a time and returns a typed
`StrategyEvaluationResult`: either one `TradeCandidate` or one structured skip with
exact input evidence. The operational cycle visits configured Pairs in deterministic
order. It does not overload `None` with every rejection cause and does not silently
drop a second eligible Pair because the current Protocol returns only one Candidate.
Milestone 2 finalizes the exact type names and migrates the Protocol before concrete
Strategy code is admitted.

The directional contract is:

```text
PairScore(base/quote) = CurrencyScore(base) - CurrencyScore(quote)

PairScore > positive threshold -> BUY eligibility
PairScore < negative threshold -> SELL eligibility
otherwise                       -> no entry Candidate
```

The operational Pair Signal is generated with
`fx_core.CurrencyPairSignalTransformer`, stored immutably, and authorized by the Live
gate before Strategy. Strategy does not reimplement `base - quote`, synthesize a
missing currency as zero, or reinterpret news/LLM payloads. Base and quote inputs must
have matching Horizon and compatible exact version metadata; otherwise no Pair Signal
is produced.

Carry must then agree:

- BUY requires an available, fresh, strictly positive long received swap;
- SELL requires an available, fresh, strictly positive short received swap; and
- zero, negative, missing, unknown, unavailable, stale, not-applicable, malformed, or
  wrong-Pair swap produces a structured skip and no Candidate.

Candidate evidence retains exact Authorized Signal, Strategy/config, Pair Signal,
and SwapQuote identities. Threshold comparisons use Pair direction. Before
implementation, the current `TradeCandidate.score: Probability` mismatch is resolved
with an explicit versioned, lossless Live contract; PairScore is never clamped into a
Probability merely to satisfy the existing field.

Strategy does not decide quantity, leverage, margin, Portfolio acceptance, Risk
approval, Execution intent, or Broker parameters. It never reads raw news text or
calls an AI provider.

### Operational inputs and time

One scheduled semantic slot has one `CycleSlotId`, derived only from:

```text
scheduled_for
as_of
execution_authority_mode
strategy_id
strategy_version
strategy_config_identity
cycle_policy_version
```

`scheduled_for` and `as_of` are UTC-aware. Input record IDs are deliberately excluded
from the slot identity: a restart or backfill must claim the existing logical slot,
not manufacture a second cycle by discovering different inputs.

The first successful claim transaction selects and freezes exactly one immutable
`CycleInputSnapshot` for that slot. Its lineage includes at least:

- Cycle Slot ID and `as_of`;
- selected Currency Signal IDs and Pair Signal IDs;
- Signal Authorization IDs and Adoption Decision IDs;
- Swap evidence IDs and cycle-time market observation IDs;
- Position snapshot/event IDs and Account snapshot ID;
- selection and freshness policy versions;
- checkpoint identity;
- canonical `input_snapshot_hash`; and
- first-write audit `captured_at`.

The semantic snapshot hash includes the canonical selected inputs and every policy
version, but excludes `captured_at`. Set-like IDs are ordered by typed ID value before
hashing; semantically ordered inputs retain explicit ordinals. One slot can have only
one snapshot. An identical retry reads it, while a conflicting second snapshot fails
closed without partial writes. The retry never searches for newer Signals,
authorizations, Swap evidence, market evidence, Positions, or Account state. Late,
backfilled, or corrected inputs are eligible only for a later slot.

`CycleAttempt` is separate append-only audit evidence. Many attempts may reference
one slot and record attempt ID, slot ID, start time, resumed stage, worker/process
identity, outcome, failure classification, and completion time. Attempt time and
worker identity are excluded from `CycleSlotId` and `input_snapshot_hash`; retries add
attempt evidence without changing the slot or frozen input.

Operational selection uses only immutable Signals present at the slot's `as_of`,
exact adoption state, public market/swap data received by that time, and Live-owned
position/account evidence. Ambiguous matching Signals fail closed; selection never
means "latest row wins" without an explicit ordering and checkpoint contract.

All application time comes from an injected Clock. Market evidence separates:

- provider quote timestamp;
- local `received_at`/availability time; and
- fill evaluation time.

A Paper fill may use only a market observation whose local `received_at` is at or
after `ApprovedExecutionIntent.created_at`, no later than its frozen `fill_due_at`,
and locally available when evaluated. It must also satisfy the configured freshness
rule. Research `ForwardResult`, future candle, or later-revised evidence is forbidden.

### Paper execution and fill evidence

`PaperExecutionGateway` accepts only approved entry, close, or emergency liquidation
intents. It has no overload for Signal, AuthorizedSignal, TradeCandidate, or an action
string. Its implementation neither imports nor constructs `GmoPrivatePostTransport`
or a real `BrokerGateway`.

Paper order lifecycle supports at least:

```text
ACCEPTED
REJECTED
OPEN
PARTIALLY_FILLED
FILLED
CANCELLED
EXPIRED
```

Illegal transitions fail before an event is appended. Current state is a projection
of immutable ordered events, not a mutable overwrite.

Append-oriented records include:

- `PaperOrder` and its lifecycle events;
- `PaperFill`;
- `PaperPositionSnapshot` or equivalent position events;
- `PaperAccountSnapshot`;
- `PaperSwapAccrual`; and
- `PaperReconciliationResult`.

Semantic identity is separated from audit time. Canonical SHA-256 content identity is
used; Python's process-dependent built-in `hash()` is forbidden.

Creating a Paper order first-writes one immutable `FillEvaluationPlan` for its exact
approved intent. The plan freezes intent ID, fill-policy/model version, market
selection policy version, spread/slippage/liquidity/partial-fill versions,
`fill_due_at` or the full versioned evaluation-boundary schedule, an explicit seed if
randomness exists, and first-write `created_at`. One intent has one plan. Retry does
not move a due time, choose a new seed, or replace a policy version.

Market selection is itself frozen before fill calculation as one immutable
`MarketObservationSelection`. Eligible evidence must:

- have the exact Currency Pair and a well-formed UTC-aware timestamp set;
- satisfy `intent.created_at <= received_at <= fill_due_at`;
- have been locally available by evaluation time;
- satisfy the versioned freshness policy and not claim a provider timestamp after
  local receipt or after the evaluation boundary; and
- be Live-owned public market evidence, never a Research `ForwardResult`.

The complete deterministic selection order is:

```sql
ORDER BY received_at ASC, provider_timestamp ASC, market_observation_id ASC
LIMIT 1
```

There is no implicit current/latest row or database natural order. The selected
observation ID, eligibility/evaluation boundary, selection-policy version, and
selection time are persisted before fill calculation and reused after restart. A
later quote cannot replace that evidence.

The fill model is immutable, versioned, and deterministic. Its identity includes at
least fill-model version, intent ID, Pair, side, Decimal quantity, exact market quote
identity/timestamps, spread model version, slippage model version, liquidity or
partial-fill model version, and an explicit seed if randomness is introduced. The
same inputs, versions, and seed produce the same order/fill evidence. There is no
implicit randomness.

No-market behavior is fixed by `fill-no-market-v1`: before `fill_due_at`, the
evaluation remains `PENDING`; at or after that boundary, absence of eligible evidence
first-writes terminal `REJECTED_NO_MARKET_EVIDENCE`. A retry reuses either the frozen
selection or terminal no-data record and cannot later fill from a newer quote. A
late-retrieved quote is eligible only when persisted local evidence proves
`received_at <= fill_due_at` and no selection/no-data record was already frozen;
`received_at > fill_due_at` is always forbidden. Research outcome data is never
borrowed and a price is never fabricated.

### Swap evidence and accrual

The operational Swap evidence contract includes Pair, long/short amounts, unit basis,
settlement currency, source/source version, captured time, effective period,
applicable rollover date, and content identity. Manual override also records its
effective period and `updated_at`.

`PaperSwapAccrual` references one exact position state, one exact Swap evidence item,
rollover date, quantity/unit conversion, settlement currency, and accrual formula
version. Missing or stale evidence yields a non-accrued diagnostic, not zero swap.
Corrections append a new evidence/accrual correction record; they do not rewrite a
historical accrual.

A dynamic GMO swap source may be implemented behind `SwapDataSource` if a supported
public contract is confirmed. This plan does not require inventing an API adapter.

### Paper account and PnL

Money and quantity use `Decimal`; binary float is not used for money, quantity, fill
price, margin, or PnL. Account evidence covers at least cash, realized PnL,
unrealized PnL, accrued swap, equity, used margin, available margin, gross exposure,
open positions, and open orders. Marking, margin, realized PnL, and swap formulas each
carry explicit versions.

Ledger entries are append-only and balanced. Account/position snapshots are derived
from exact ordered entries and can be rebuilt and compared during reconciliation.
Snapshot IDs commit to their inputs and formula versions.

### Recovery and reconciliation

Each scheduled cycle has the single persistent `CycleSlotId` defined above. Its
inputs are frozen once in `CycleInputSnapshot`, and each worker run is a separate
`CycleAttempt`. Future persistence enforces one slot row and one input-snapshot row
per semantic slot. First claim, input selection, and snapshot persistence are one
transaction; a best-effort selection followed by a later insert is not sufficient.

Swap, Account, Position, Signal, Authorization, and other selected input evidence
remain fixed across retries. Fill, ledger, Position, and Account outputs append after
the input snapshot; they never become or rewrite that slot's inputs.

Restart and retry must:

- resume or explicitly fail an incomplete cycle;
- never create a second Paper order for one approved intent;
- never duplicate a fill, ledger entry, or swap accrual;
- reconcile orders, fills, positions, account, and cycle state before new work;
- preserve the first successful semantic records; and
- append recovery/reconciliation evidence rather than mutate history.

One process-level overlap lock prevents two recurring workers from owning the same
cycle. A crashed lock cannot permanently block recovery.

### Invariants

- `PAPER_EXECUTED != LIVE_EXECUTED`; burn-in never promotes authority.
- ExecPlan 0006 cannot select, construct, import, or invoke a real Broker Private
  transport in its operational composition.
- Signal remains immutable and currency-first; Pair transformation is versioned and
  shared through `fx_core`.
- A missing base or quote currency Signal is not neutral zero.
- Only exact Authorized Signals reach Strategy.
- Strategy emits candidates, never order or quantity decisions.
- Portfolio and Risk remain mandatory for entry and ordinary close paths.
- Risk emergency liquidation remains distinct from Strategy exit.
- Execution/Paper Gateway accepts approved intents only.
- Currency Exposure includes positions and pending intents/orders.
- Unknown/unavailable/stale/malformed swap is not zero.
- Fill and swap evidence has no future leakage.
- `PAPER` uses Adoption `RuntimeMode.SHADOW`; it cannot create a
  `RuntimeMode.LIVE` authorization or Live rollout authority.
- One semantic Cycle Slot has one frozen input snapshot; retry adds attempts and does
  not reselect inputs.
- One approved intent has one frozen fill plan and one immutable selected-market or
  terminal no-market decision.
- Paper orders, fills, positions, account, PnL, swap, cycle, and reconciliation
  evidence are append-oriented and versioned.
- Private POST automatic retry remains prohibited and Live two-step arming remains
  unchanged.

## Milestones

### Milestone 1 - Plan and Production Strategy Contract

Contribution: fixes the Strategy and Paper/Live authority semantics before an
operational implementation can accidentally inherit shadow or real-Broker behavior.

Deliverables:

- Register ExecPlans 0006 and 0007 in the Program roadmap.
- Add this living ExecPlan and ADR 0008.
- Record the exact current contracts and gaps above.
- Specify `NewsFilteredCarryStrategy`, immutable versioned config, supported Pair
  scope, structured skip semantics, and shared Pair transformation use.
- Specify explicit `SHADOW_NOT_SUBMITTED`/`PAPER`/`LIVE` authority and the fixed
  mapping to existing Adoption `RuntimeMode` without adding a Paper runtime mode.
- Specify `CycleSlot`/first-write `CycleInputSnapshot`/`CycleAttempt` separation,
  exact `FillEvaluationPlan`, deterministic market selection, and versioned terminal
  no-market behavior.
- Specify Paper order/fill/ledger/swap/time/recovery evidence and the separate entry,
  close, and emergency-liquidation branches.
- Update architecture, Swap Bot, data/versioning, repository, test strategy, and
  design index documents with clearly labeled Current and Target sections.

Files expected to change: documentation and ADR files only.

Observable behavior: no production runtime behavior changes; the existing full test,
lint, and type-check matrix remains green and the repository contains no Paper or
Live submission implementation implied by the target documentation.

Verification:

```powershell
python -m pytest -q
python -m ruff check .
python -m mypy packages/fx_core/src packages/fx_signal_store/src apps/fx_research/src apps/swap_bot/src
```

### Milestone 2 - Production Strategy Implementation

Contribution: replaces the test-only Strategy seam with deterministic production
choice while preserving adoption, Portfolio, Risk, and Broker separation.

Deliverables:

- Frozen `NewsFilteredCarryStrategyConfig` with canonical identity.
- Live-owned operational Signal source/checkpoint Port and SQLite adapter.
- Explicit authority-to-adoption mapping and contract tests: both
  `SHADOW_NOT_SUBMITTED` and `PAPER` authorize Signals with `RuntimeMode.SHADOW`;
  `LIVE` is rejected before authorization or cycle start in ExecPlan 0006.
- Operational Pair Signal production via `CurrencyPairSignalTransformer`, never a
  duplicate formula in `swap_bot`.
- Exact AuthorizedSignal input and deterministic entry Candidate/structured skip
  output for `USD_JPY` and `MXN_JPY` only.
- Fresh positive received-carry gate with exact SwapQuote lineage.
- Explicit resolution of Candidate PairScore evidence without clamping.
- Separate ordinary close Candidate/approved close intent contract, partial close,
  reduce-only enforcement, and structured exit reasons; retain
  `ApprovedLiquidationIntent` for Risk emergency only.
- Architecture and behavior tests proving no AI, Research evaluator, Execution, or
  Broker dependency enters Strategy.

Observable behavior: identical authorized Signals, config, swap evidence, positions,
and clock yield the same Candidate or skip reason. Neutral, misaligned, non-positive,
missing, stale, malformed, or wrong-Pair carry yields no entry. No approved intent is
created inside Strategy. A Paper cycle can consume either `SHADOW_ONLY` or
`LIVE_ELIGIBLE` adoption through `RuntimeMode.SHADOW`, but cannot create
`RuntimeMode.LIVE` authorization or real Broker authority.

Verification:

```powershell
python -m pytest -q tests/strategy tests/swap tests/candidate_authorization tests/architecture
python -m ruff check .
python -m mypy apps/swap_bot/src packages/fx_core/src
```

### Milestone 3 - Paper Broker and Ledger

Contribution: provides realistic but fictional execution evidence without granting
or sharing real Broker authority.

Deliverables:

- `PaperExecutionGateway` in an isolated Paper infrastructure module.
- Immutable Paper order lifecycle, deterministic versioned fill model, exact market
  quote evidence, partial-fill/cancel/expiry behavior, and deterministic IDs.
- One immutable `FillEvaluationPlan` per approved intent, with fixed due boundary,
  model/policy versions, and seed; retry cannot move or replace them.
- One immutable `MarketObservationSelection` before fill calculation, using exact
  post-intent/pre-due eligibility and deterministic `received_at`, provider timestamp,
  and observation-ID ordering.
- Versioned `PENDING` then terminal `REJECTED_NO_MARKET_EVIDENCE` behavior that cannot
  be revised into a later fill by retry.
- Entry, ordinary reduce-only close, partial close, and Risk liquidation handling.
- Append-only Paper order/fill/position/account/ledger/PnL/swap/reconciliation schema,
  beginning with additive Live migration `0003`.
- Decimal money/quantity and versioned marking, margin, realized/unrealized PnL, and
  swap formulas.
- Persistence-boundary authenticity, legal-transition, balance, lineage,
  idempotency, and immutability checks.
- Architecture tests that Paper infrastructure cannot import or construct the real
  Private transport.

Observable behavior: exact intent and evidence replay produces exact Paper records;
restart cannot duplicate them. Invalid or forged records leave no partial rows. No
pre-intent, post-due, locally unavailable, future, or Research Forward Result can
create a fill. Equal-timestamp evidence uses the explicit observation-ID tie-breaker,
and restart reuses the persisted selection even if a newer quote exists.

Verification:

```powershell
python -m pytest -q tests/paper_domain tests/paper_execution tests/paper_persistence tests/architecture
python -m ruff check .
python -m mypy apps/swap_bot/src
```

### Milestone 4 - Operational Paper Cycle

Contribution: proves that real operational inputs can traverse the production
Strategy and paper ledger once, and can recover safely after interruption.

Deliverables:

- One-shot production cycle with injected Clock and explicit authority mode.
- Operational Signal, market quote, SwapQuote, position, account, and adoption input
  adapters.
- `CycleSlotId` derived from schedule/as-of, execution authority, Strategy/config,
  and cycle-policy version, excluding variable input records.
- Atomic first claim and immutable `CycleInputSnapshot` freeze, with one-slot/one-
  snapshot constraints, canonical input/policy hash, checkpoint identity, and stable
  first-write `captured_at`.
- Append-only `CycleAttempt` audit, overlap claim, checkpoint, and structured
  incomplete/failure state; retry adds an attempt but reuses the slot and snapshot.
- Startup reconciliation and explicit recovery of crashes between intent/order,
  order/fill, fill/ledger, and ledger/cycle completion.
- Stable idempotency across process restart for orders, fills, ledger, and accrual.
- `SHADOW_NOT_SUBMITTED` and `PAPER` composition roots; `LIVE` fails configuration.
- Real Broker tripwire/probe proving zero construction and submit calls.

Observable behavior: one operational cycle reaches either structured skip/rejection,
`NOT_SUBMITTED`, or Paper evidence. Re-running the same cycle after any simulated
crash converges to one logical result with no duplicate order/fill. Stale or
ambiguous input fails closed. Signals, authorizations, Swap evidence, market evidence,
Positions, or Account state arriving after first claim cannot change the historical
snapshot. A conflicting second snapshot is rejected atomically. Real Broker calls
remain exactly zero by observation, not a hardcoded summary.

Verification:

```powershell
python -m pytest -q tests/operational_paper tests/recovery tests/reconciliation tests/broker_contract
python -m swap_bot paper-once --config <paper-config> --as-of <utc-time>
python -m ruff check .
python -m mypy apps/swap_bot/src
```

### Milestone 5 - Scheduler, Observability, and Burn-in

Contribution: demonstrates repeatable continuous Paper operation and produces the
evidence that a separate ExecPlan 0007 review may inspect, without promoting itself.

Deliverables:

- Recurring daemon/scheduler with single-owner overlap prevention, bounded graceful
  shutdown, startup reconciliation, and durable checkpointing.
- Structured health, cycle latency, data freshness, skip/rejection, order/fill,
  reconciliation, PnL, swap, and zero-real-Broker-call observability.
- Explicit alert policy for stale/missing inputs, failed recovery, ledger mismatch,
  repeated partial fill, overlap, and process restart.
- Versioned BurnInAcceptancePolicy with configured duration/sample requirements and
  immutable BurnInReport linked to exact Strategy/config/fill/swap/PnL versions.
- Restart and failure-injection exercises with recorded outcomes.
- An explicit 0007 readiness report that can say ready or not ready but cannot grant
  Live authority.

Observable behavior: the daemon resumes without duplicates, continuously reconciles
its ledger, and emits one auditable burn-in report. The report never creates or
modifies a Live rollout decision. `LIVE` remains impossible.

Verification:

```powershell
python -m pytest -q
python -m ruff check .
python -m mypy packages/fx_core/src packages/fx_signal_store/src apps/fx_research/src apps/swap_bot/src
python -m swap_bot paper-burn-in-report --config <paper-config>
```

## Migration and compatibility

- Existing immutable Signal, Research validation, Live adoption, Candidate,
  Portfolio, Risk, intent, and `NOT_SUBMITTED` records are not rewritten.
- Existing `SHADOW` runtime authorization rows remain readable. Operational authority
  uses the fixed compatibility mapping above rather than adding `RuntimeMode.PAPER`
  or reinterpreting stored values.
- Numbered Live schema migration starts at `0003`; no inline historical table is
  renumbered.
- Paper tables are additive and use append-only guards. Paper projections can be
  rebuilt from their evidence records.
- The fixture-only `shadow-once` command remains characterization evidence until a
  later explicit removal decision. It does not become the production scheduler.
- Existing `ExecutionService` and `GmoPrivatePostTransport` safety behavior is not
  weakened. Paper composition does not share their transport.
- `USD_JPY` and `MXN_JPY` are an explicit new Strategy configuration, not a silent
  expansion of the current fixture. Additional Pairs require a new config identity,
  adoption evidence, tests, and rollout decision.
- Research forward market adapters and `ForwardResult` remain Research-owned and are
  not reused for Paper fill.
- If a dynamic swap provider cannot satisfy source, basis, version, effective-time,
  and content-identity requirements, it is not admitted; manual evidence remains
  explicit rather than fabricated.

## Acceptance criteria

ExecPlan 0006 is complete only when all of the following are true:

- One versioned production `NewsFilteredCarryStrategy` consumes exact Authorized
  Signals and cannot bypass Portfolio or Risk.
- Currency-first Pair transformation uses the shared `currency-pair-v1` contract and
  exact stored lineage; missing currency evidence is not treated as zero.
- The initial eligible Pair set is exactly `USD_JPY` and `MXN_JPY` in immutable
  config.
- Threshold, freshness, Pair set, Strategy, exit, swap, fill, margin, and PnL
  semantics are versioned and reproducible.
- BUY/SELL entry requires matching strictly positive received swap; all missing,
  stale, malformed, zero, or negative cases fail closed with structured reasons.
- Ordinary Strategy close, partial reduce-only close, and Risk emergency liquidation
  have distinct typed authority and lineage.
- `SHADOW_NOT_SUBMITTED`, `PAPER`, and `LIVE` are explicit distinct modes; 0006 can
  run only the first two.
- `SHADOW_NOT_SUBMITTED` and `PAPER` both request Adoption `RuntimeMode.SHADOW` while
  preserving their distinct execution authority in operational lineage; Paper never
  creates `RuntimeMode.LIVE` authorization.
- Only approved intents create Paper orders. Signal/Candidate cannot call a Paper or
  real Broker Gateway directly.
- Paper orders, fills, positions, account, PnL, swap, cycles, and reconciliation are
  immutable append-oriented evidence with deterministic semantic identities.
- Fill uses only post-intent available market evidence and never a Research Forward
  Result or future observation.
- One semantic Cycle Slot has one immutable first-write input snapshot; retry,
  restart, late data, and backfill cannot create a second cycle or alter its meaning.
- One approved intent has one immutable fill plan and deterministic frozen market
  selection or terminal no-market decision; due boundary, seed, and first-write audit
  metadata remain stable across retry.
- Money/quantity/PnL uses Decimal and explicit formula versions.
- Restart/retry/reconciliation converges without duplicate order, fill, ledger entry,
  or swap accrual.
- Continuous Paper operation produces versioned burn-in evidence and observed real
  Broker construction/submit calls remain zero.
- `PAPER_EXECUTED` cannot create `LIVE_EXECUTED`, change arming, or grant ExecPlan
  0007 authority.
- Full tests, Ruff, and strict mypy pass on Python 3.11 and 3.14.

## Progress

- [x] (2026-07-16) Read repository instructions, `PLANS.md`, Swap Bot instructions,
  relevant Skills, Program/architecture/domain/Research/Live/data/repository/test
  documents, ExecPlans 0001/0005, and ADR 0007.
- [x] (2026-07-16) Confirmed the exact clean baseline and inspected Strategy,
  Candidate, exit/liquidation, Swap, Position/Account, Broker, Execution,
  idempotency, migration, CLI, scheduler, Paper, and configuration state.
- [x] (2026-07-16) Created the ExecPlan 0006 living document and registered
  ExecPlan 0007 as the separate Controlled Live rollout.
- [x] (2026-07-16) Defined the production Strategy and Paper/Live authority target,
  Paper evidence model, recovery boundaries, and reviewable ADR without runtime
  implementation.
- [x] (2026-07-16) Passed the full local Python 3.11/3.14 test, Ruff, and strict mypy
  matrix for the planning-only diff.
- [x] (2026-07-17) Finalized the PAPER-to-Adoption mapping and the first-write
  Cycle/Input/Fill identity contract across ExecPlan 0006, ADR 0008, architecture,
  Swap Bot, data/versioning, and future-test documentation without runtime changes.
- [x] (2026-07-17) Passed the final planning-contract diff through the full local
  Python 3.11/3.14 test, Ruff, and strict mypy matrix.
- [ ] Milestone 2 - Production Strategy Implementation.
- [ ] Milestone 3 - Paper Broker and Ledger.
- [ ] Milestone 4 - Operational Paper Cycle.
- [ ] Milestone 5 - Scheduler, Observability, and Burn-in.

## Surprises & discoveries

- Observation: the current Strategy Port is already authorization-aware, but every
  concrete Strategy is test support or fixture construction.
  Evidence: `swap_bot.ports`, `tests/adoption_shadow`, and `swap_bot.shadow`.
  Resolution: keep the Port direction, replace `None` with a typed per-Pair evaluation
  result, and implement one production Strategy in Milestone 2; do not promote the
  fixture adapter or lose a second Pair because the current Port returns one item.
- Observation: the existing Candidate is entry-only, while the only liquidation
  intent is a Risk margin-kill-switch artifact and cannot pass the current
  `ExecutionService`/`BrokerGateway` contract.
  Evidence: `swap_bot.models`, `swap_bot.risk`, and `swap_bot.execution`.
  Resolution: design a separate ordinary reduce-only close branch before Paper
  implementation; preserve emergency liquidation as separate Risk authority.
- Observation: `TradeCandidate.score` is a `Probability`, but the shared Pair
  direction is a `PairScore` in `[-2, 2]`.
  Resolution: Milestone 2 must preserve Pair direction in an explicit versioned Live
  contract. Silent clamp or unversioned normalization is prohibited.
- Observation: Swap availability/freshness is modeled, but the current quote lacks
  the unit, settlement, rollover, version, and content identity required to accrue
  Paper cash.
  Resolution: extend Live-owned Swap evidence before ledger accrual; do not infer
  units from provider names.
- Observation: Account state is only a margin ratio, and Position state has no entry
  price or realized/unrealized PnL lineage.
  Resolution: Paper ledger/account design precedes the fill implementation and is
  reconciled from append-only events.
- Observation: current `ExecutionService` is not a Paper simulator; it never calls
  its Broker Gateway and only emits `NOT_SUBMITTED`.
  Resolution: keep it as shadow characterization and add a separate
  `PaperExecutionGateway`, not a branch controlled by a dry-run boolean.
- Observation: no production settings exist. USDJPY and safety values found in tests
  are fixtures, while MXNJPY is not currently configured.
  Resolution: create an exact `USD_JPY`/`MXN_JPY` immutable production config in
  Milestone 2 and require review for every value.
- Observation: Live numbered migrations currently end at `0002`.
  Resolution: Paper persistence begins with additive migration `0003` and does not
  rewrite the inline base schema.
- Observation: parallel local pytest processes attempted to share pytest's default
  Windows temporary/cache roots and produced permission/setup errors unrelated to
  the repository.
  Resolution: rerun each interpreter with a distinct workspace `--basetemp`; both
  complete suites passed. CI jobs are already isolated by matrix runner.
- Observation: the initial plan left the mapping between PAPER execution authority
  and the existing Adoption runtime enum for Milestone 2, although the latter has
  only SHADOW and LIVE.
  Resolution: PAPER is fictional and therefore uses `RuntimeMode.SHADOW`; execution
  authority remains a separately persisted lineage dimension, and only ExecPlan 0007
  may use the LIVE mapping.
- Observation: including selected input IDs in cycle identity permits a restart to
  derive another logical cycle for the same schedule slot after late/backfilled data
  arrives.
  Resolution: identify the stable `CycleSlot` without input IDs, atomically freeze one
  `CycleInputSnapshot` on first claim, and append retry-specific `CycleAttempt`
  evidence.
- Observation: a deterministic fill formula does not make replay deterministic when
  retry can move its due boundary or select a newer eligible quote.
  Resolution: first-write one `FillEvaluationPlan` and persist the exact ordered
  market selection or terminal no-market evidence before fill calculation.
- Observation: the sandboxed first validation attempt could not create its configured
  pytest base directory and therefore produced setup errors rather than test failures.
  Resolution: rerun both supported interpreters against distinct OS temporary roots;
  both full suites passed, and Ruff/mypy ran without cache writes.

## Decision log

- 2026-07-16: Add ExecPlan 0006 for production Strategy plus Paper operations and
  reserve ExecPlan 0007 exclusively for Controlled Live execution rollout.
- 2026-07-16: Treat `SHADOW_NOT_SUBMITTED`, `PAPER`, and `LIVE` as distinct execution
  authorities. A single dry-run flag cannot model them.
- 2026-07-16: Define Paper execution as a separate adapter and evidence domain that
  cannot import or construct the real Broker transport; see ADR 0008.
- 2026-07-16: Use `NewsFilteredCarryStrategy` with initial exact Pair scope
  `USD_JPY`/`MXN_JPY`, shared `currency-pair-v1` semantics, explicit directional
  thresholds, and strictly positive matching received swap.
- 2026-07-16: Require pair Signals to be materialized through the shared transformer
  and persisted before authorization; Strategy does not duplicate `base - quote` or
  treat missing currency evidence as neutral.
- 2026-07-16: Keep Strategy entry Candidate, ordinary close, and Risk emergency
  liquidation as separate typed paths; do not add an action string to
  `TradeCandidate`.
- 2026-07-16: Make Paper fill, swap, ledger, PnL, cycle, and reconciliation records
  versioned deterministic evidence and prohibit lookahead/Research Forward data.
- 2026-07-16: Start additive Paper persistence at Live migration `0003` after current
  migrations `0001`/`0002`.
- 2026-07-17: Map both `SHADOW_NOT_SUBMITTED` and `PAPER` to Adoption
  `RuntimeMode.SHADOW`; retain execution authority as a separate enum/lineage field,
  add no Paper runtime mode, and reserve the LIVE mapping for ExecPlan 0007.
- 2026-07-17: Define one `CycleSlot` per schedule/as-of/authority/Strategy/policy
  tuple, one atomically first-written immutable `CycleInputSnapshot` per slot, and
  append-only `CycleAttempt` records for retry audit.
- 2026-07-17: Freeze one `FillEvaluationPlan` per approved intent and one immutable
  market selection or `fill-no-market-v1` terminal outcome; order evidence by local
  receipt, provider timestamp, then observation ID and never reselect after first
  write.

## Validation

Planning-milestone validation is recorded here after both supported local Python
interpreters run the CI-equivalent commands. No external smoke test is required for a
documentation-only architecture milestone.

```powershell
python -m pytest -q
python -m ruff check .
python -m mypy packages/fx_core/src packages/fx_signal_store/src apps/fx_research/src apps/swap_bot/src
```

Completed locally on 2026-07-16 with CI-equivalent commands:

- Python 3.11.9: `289 passed, 5 skipped`; Ruff passed; strict mypy passed for
  63 source files.
- Python 3.14.6: `289 passed, 5 skipped`; Ruff passed; strict mypy passed for
  63 source files.
- The five skips are opt-in external provider smoke tests.
- Both pytest runs used separate workspace `--basetemp` directories to avoid a
  Windows parallel-run collision. Pytest reported a non-failing cache warning because
  the two local processes still shared `.pytest_cache`; result collection was not
  affected.
- Program numbering is consistent from 0001 through 0007. ExecPlan 0006 owns only
  production Strategy/Paper operations; ExecPlan 0007 exclusively owns real Broker
  rollout.
- The diff contains documentation and ADR files only. No Paper implementation,
  Broker adapter, Private POST, arming, Portfolio/Risk/Execution behavior, or 0007
  implementation changed.
- GitHub-hosted matrix confirmation remains pending until this local planning commit
  is pushed.

Final planning-contract revision completed locally on 2026-07-17:

- Python 3.11.9: `289 passed, 5 skipped`; Ruff passed; strict mypy passed for
  63 source files.
- Python 3.14.6: `289 passed, 5 skipped`; Ruff passed; strict mypy passed for
  63 source files.
- Pytest used distinct OS temporary roots with cache disabled after the sandboxed
  base-directory creation attempt was denied. The denied attempt was an environment
  setup failure; the clean reruns above are the recorded result.
- Ruff used `--no-cache` and mypy used `--no-incremental` for the final matrix.
- `git diff --check` passed, and the diff remains limited to the six requested
  planning/architecture documents.
- GitHub Actions has not been independently observed for this unpushed revision, so
  no hosted-CI success is claimed here.
