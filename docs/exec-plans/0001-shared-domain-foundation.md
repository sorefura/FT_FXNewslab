# ExecPlan 0001: Shared Domain Foundation and Live Boundary Migration

## Goal

Introduce the minimum immutable domain contract shared by Research and Live Trading, then prove
that a Live consumer can carry a Signal through explicit Strategy, Portfolio, Risk, and approved
Execution boundaries without submitting an order.

## Non-goals

- Migrating or cleaning the legacy Swap Bot
- Operational news collection and scheduling
- Production Strategy or live cutover
- Research metrics, validation, or `FT_NewsScoring` migration
- Converting historical AI actions into Signals

## Current state

The legacy Bot combines news, AI action, risk mutation, sizing, and execution. It has no Feature
or Signal record and no Currency Exposure aggregation. Its inspected files and preserved safety
behaviors are recorded in `docs/migration/legacy-swap-bot-inventory.md`; legacy code is not copied.

## Target architecture

```text
Observation -> Feature -> immutable Signal
                              |
                              +-> Research consumer contract
                              +-> Strategy -> Portfolio -> Risk -> approved intent -> Execution
```

`fx_core` owns shared vocabulary. `fx_signal_store` owns the SQLite reference adapter. Live-only
decision types and persistence remain in `swap_bot`. Research and Live never import each other.

## Invariants

- Signal and lineage are immutable ex-ante records.
- AI produces Features, never actions.
- Currency Signals are primary; Pair score is base minus quote.
- UTC-aware timestamps distinguish publication from first availability.
- Execution accepts only approved intents.
- Risk, Portfolio, and Candidate IDs must describe one consistent decision chain.
- Unknown, unavailable, and stale swap are not zero.
- New orchestration never calls Broker submit; tests observe the injected gateway rather than a
  result field populated with a constant.

## Milestones

### Milestone 1 — Shared Domain Foundation

Deliver Python 3.11 tooling, shared value objects, immutable Signal, version metadata, Program
roadmap, and import-boundary tests.

### Milestone 2 — Observation to Signal Shadow Foundation

Deliver NewsObservation, CurrencyFundamentalFeature, provider-neutral LLM extraction, deterministic
versioned scoring, Currency-to-Pair transformation, append-only SQLite lineage, and a test-only
Research consumer contract.

### Milestone 3 — Live Boundary Seam

Deliver Live-only Candidate/Portfolio/Risk/Intent/Result types, approved-intent-only dry-run
Execution, persistent idempotency, and contract tests for dual arming and non-retried Private POST.

### Milestone 4 — Swap, Exposure, and Risk Shadow Evaluation

Deliver explicit swap availability, source selection, Currency Exposure including pending intents,
structured Portfolio decisions, independent Risk decisions, and liquidation intent creation.

### Milestone 5 — Shadow Decision Chain Validation

Run one recorded offline cycle from Observation through approved intent and persist `NOT_SUBMITTED`.
Assert that an injected counting BrokerGateway observes zero submit calls. Persisted Risk,
Portfolio, and Candidate IDs must form one consistent chain.

## Migration and compatibility

Only investigation evidence is retained from the legacy repository. New SQLite records begin with
ExecPlan 0001; historical JSONL is not backfilled. Secrets and runtime files are excluded.

## Validation

```powershell
python -m pytest -q
python -m ruff check .
python -m mypy packages apps
python -m swap_bot shadow-once --fixture tests/fixtures/shadow_cycle.json
```

CI runs the same checks on Python 3.11 and the latest supported version.

## Decision log

- 2026-07-13: Shared Signal contract is the primary objective; Live is its first boundary consumer.
- 2026-07-13: Python baseline is 3.11.
- 2026-07-13: Legacy snapshot and cleanup are excluded.
- 2026-07-13: Research compatibility is proven by contract tests, not an application implementation.
- 2026-07-13: SQLite is a separate shared infrastructure package; see ADR-0005.
- 2026-07-13: Risk, Portfolio, and Candidate ID consistency is checked before intent creation and
  again at the SQLite intent boundary.
- 2026-07-13: Shadow Broker non-submission is verified through an injected counting gateway;
  `broker_submit_calls` is not emitted as a hardcoded result.

## Progress

- [x] Read repository design, ADRs, and applicable Skills.
- [x] Inventory the legacy Swap Bot and record migration evidence.
- [x] Milestone 1 — Shared Domain Foundation.
- [x] Milestone 2 — Observation to Signal Shadow Foundation.
- [x] Milestone 3 — Live Boundary Seam.
- [x] Milestone 4 — Swap, Exposure, and Risk Shadow Evaluation.
- [x] Milestone 5 — Shadow Decision Chain Validation.

Verification completed on 2026-07-13 with Python 3.11.9:

- `55 passed`
- Ruff: all checks passed
- mypy: no issues in 25 source files
- Shadow result: complete and ID-consistent decision chain, `NOT_SUBMITTED`
- An injected counting BrokerGateway observed zero submit calls in the shadow test.

Python 3.11 compatibility is verified by the package metadata, Ruff and mypy targets, CI matrix,
and an actual local Python 3.11 CI-equivalent run.
