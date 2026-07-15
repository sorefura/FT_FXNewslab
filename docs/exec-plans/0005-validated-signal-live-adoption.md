# ExecPlan 0005: Validated Signal Live Adoption

This document is a living ExecPlan. Maintain `Progress`, `Surprises & Discoveries`,
`Decision Log`, and `Validation` while implementation proceeds. This plan follows
`PLANS.md`.

## Program context

FXNewslab deliberately separates Research validation from Live authority:

```text
Research discovers.
Signal describes.
Strategy chooses.
Portfolio allocates.
Risk permits.
Execution performs.
```

ExecPlan 0004 can produce `VALIDATED_FOR_RESEARCH`. That status is evidence, not
permission to use a Signal in Strategy and not permission to place an order. This
ExecPlan introduces an explicit, append-only adoption boundary:

```text
VALIDATED_FOR_RESEARCH
        -> explicit Strategy adoption approval
        -> runtime Signal authorization
        -> Strategy
        -> Portfolio -> Risk -> Execution
```

`APPROVED_FOR_STRATEGY` remains distinct from `ORDER_APPROVED`. Broker submission is
not enabled by this plan.

## Goal

Allow an operator to select one immutable Research ValidationAssessment by ID,
snapshot and verify its exact evidence into the Live database, apply a time-bounded
exact-match StrategyAdoptionPolicy, and append an explicit approval or revocation.
At runtime, authorize only Signals that match one unrevoked approval exactly before
they reach Strategy. Persist authorization lineage through TradeCandidate and prove
the full path in shadow mode with zero Broker submit calls.

## Non-goals

- Automatic promotion from Research status, metrics, latest assessment, or latest
  policy.
- Recomputing Research metrics or interpreting thresholds in Live.
- Wildcards, partial cohort matching, implicit version fallback, or retroactive
  activation of older Signals.
- Importing `fx_research` from `swap_bot`, or reading the Research database at
  runtime.
- A new production Strategy, sizing change, Portfolio/Risk rule change, Execution
  redesign, live cutover, or actual Broker enablement.
- Changing Signal, ForwardResult, Evaluation, or validation formulas.

## Current state

- Baseline HEAD is `f075cdf4d7251da9b9707bcf4815a56ae9fd4568`, clean and synchronized
  with `origin/main` at plan start.
- Research owns append-only Evaluation Run, Report, Validation Policy, Assessment,
  and full input snapshot records. The snapshot contract is
  `evaluation-input-snapshot-v2`.
- `swap_bot` already owns immutable Candidate, Portfolio, Risk, Execution intent,
  and Order result records. Execution accepts approved intent only, defaults to
  dry-run, and the existing shadow path never submits to a Broker.
- No production Strategy exists. The current shadow fixture is a temporary candidate
  source used to verify boundaries.
- Live persistence is currently initialized by an inline base schema. The first
  numbered Live migration can therefore be additive without rewriting historical
  schema.

## Target architecture

```text
Research SQLite (approval time, read-only)
  assessment -> report -> run -> full input snapshot
             -> Research policy
                  |
                  v
       ResearchValidationEvidenceSource port
                  |
        explicit approve --apply transaction
                  v
Live SQLite
  evidence snapshot + adoption policy + approval/revocation
                  |
        runtime LiveAdoptionGate (Live DB only)
                  v
  SignalAuthorization -> AuthorizedSignal -> Strategy
                  v
  Candidate authorization lineage
                  v
       Portfolio -> Risk -> Execution -> NOT_SUBMITTED
```

`swap_bot` owns the port contract and the read-only SQLite adapter. It imports only
shared `fx_core` Signal types and never imports Research application modules.

## Public contracts and invariants

`ResearchValidationEvidenceSource` reads one assessment by explicit ID in one SQLite
read transaction. It checks exact assessment/report/run/policy/snapshot lineage,
supported snapshot version, structural JSON, stored content hashes, and timestamps.
It does not choose a latest record and never writes to the Research database.

`ResearchValidationEvidenceSnapshot` stores assessment ID/status/time, report and run
IDs, Research policy version/hash/payload, cohort identity/hash/payload, Evaluation
input snapshot version/hash/payload, and its own contract version/hash. All payloads
are canonical JSON and append-only.

`StrategyAdoptionPolicy` requires exact Strategy ID/version, exact Research policy
version, exact strict cohort dimensions, adoption mode (`SHADOW_ONLY` or
`LIVE_ELIGIBLE`), `effective_from`, and `expires_at`. Every nullable semantic version
matches `None` exactly. Wildcards and open-ended validity are invalid.

An approval or revocation is a separate immutable decision with actor, reason,
decision time, evidence/policy identity, and deterministic semantic identity.
Dry-run is the default. `--apply` writes the evidence snapshot, policy, and approval
atomically. A revocation references the exact prior approval and never mutates it.

Runtime authorization fails closed on zero or multiple matches, revocation, expired
or not-yet-effective periods, pre-effective Signal creation, target/horizon/version
mismatch, and `SHADOW_ONLY` use in Live mode. The runtime gate reads no Research data.
Each authorization records Signal, approval, policy, evidence, Strategy, runtime mode,
and authorization time. Candidate persistence in the new adoption path requires one
valid authorization for every contributing Signal and rechecks current revocation and
validity state.

## Milestones

### Milestone 1 - Evidence snapshot and explicit adoption decisions

Contribution: turns Research validation into immutable Live evidence without granting
implicit Strategy authority.

- Add this ExecPlan and an ADR defining the boundary.
- Add a read-only assessment-ID Research evidence port/SQLite adapter.
- Add strict cohort, evidence snapshot, policy, approval, and revocation contracts.
- Add the first numbered additive Live migration and append-only repositories.
- Add dry-run-by-default approve/revoke one-shot CLI commands with explicit `--apply`.
- Verify EXPERIMENTAL/PROMISING, malformed lineage/hash/JSON, unsupported snapshot,
  policy mismatch, conflict, atomicity, idempotency, and immutability behavior.

### Milestone 2 - Runtime adoption gate and Signal authorization

Contribution: ensures only an exact, current, unrevoked approval can cross into
Strategy without runtime dependency on Research.

- Match all Signal semantic dimensions exactly.
- Enforce time window, no retroactive activation, mode, ambiguity, and revocation.
- Persist append-only authorization lineage and structured fail-closed reason codes.
- Prove Signal immutability and zero Research DB reads after approval.

### Milestone 3 - Candidate authorization lineage

Contribution: prevents Strategy output from losing the authority chain before
Portfolio and Risk.

- Change the Strategy port to consume authorized Signal envelopes.
- Add an atomic strict Candidate append path that validates every Signal
  authorization and stores Candidate-to-authorization lineage.
- Recheck revocation/time/Strategy identity at Candidate persistence so a stale
  authorization cannot bypass current state.
- Keep the old fixture-only Candidate append seam for the existing ExecPlan 0001
  shadow characterization path; production adoption uses only the strict path.

### Milestone 4 - Authorized shadow decision cycle

Contribution: proves the new evidence-to-Live chain composes with existing
Portfolio/Risk/Execution boundaries without creating order authority.

- Run validated evidence -> explicit approval -> runtime authorization -> test
  Strategy -> strict Candidate persistence -> Portfolio -> Risk -> Execution.
- Persist exact decision and authorization lineage.
- Record `NOT_SUBMITTED` and assert the injected Broker submit probe remains zero.

### Milestone 5 - Documentation and final validation

Contribution: makes the operational boundary auditable and confirms it on both
supported Python versions.

- Update Program, architecture, Research, Swap Bot, data/versioning, repository,
  test strategy, and documentation index.
- Run all tests, Ruff, strict mypy, and Python 3.11/3.14 CI.
- Record actual results below and use milestone commits whose bodies explain Why.

## Acceptance criteria

- `VALIDATED_FOR_RESEARCH`, `APPROVED_FOR_STRATEGY`, and order approval remain three
  distinct authorities.
- Evidence selection is explicit by assessment ID, read-only, consistent, canonical,
  and append-only in Live.
- Approval and revocation require explicit apply; dry-run has no Live writes.
- Strategy adoption policies are immutable, time-bounded, exact-match, and have no
  wildcard semantics.
- Runtime reads only Live state and fails closed for all missing, ambiguous, stale,
  revoked, mismatched, or mode-ineligible conditions.
- Signal authorization and Candidate authorization lineage are append-only and exact.
- Existing Portfolio/Risk/Execution boundaries remain intact.
- One full authorized shadow cycle ends `NOT_SUBMITTED`; observed Broker submit calls
  equal zero by an injected probe rather than a hardcoded result.
- Full tests, Ruff, and strict mypy pass on Python 3.11 and 3.14.

## Progress

- [x] (2026-07-15) Read repository instructions, relevant Skills, Program/architecture/
  Research/Live/data/test documents, and ExecPlans 0001/0004.
- [x] (2026-07-15) Confirmed exact clean baseline and inspected Research and Live
  persistence/contracts.
- [x] (2026-07-15) Created ExecPlan 0005 before implementation.
- [x] (2026-07-15) Milestone 1 - Added the read-only exact-assessment evidence
  adapter, immutable evidence/policy/decision contracts, the first additive Live
  migration, atomic/idempotent approval and revocation persistence, and dry-run-first
  one-shot CLI commands.
- [x] (2026-07-15) Milestone 2 - Added the Live-only exact-match adoption gate,
  bounded/revocable/mode-aware/no-retroactive checks, immutable idempotent Signal
  authorization lineage, and structured fail-closed reasons.
- [x] (2026-07-15) Milestone 3 - Changed the Strategy port to authorized envelopes,
  added an atomic strict Candidate append path and additive lineage-integrity
  migration, and revalidated persisted authorization, approval period, revocation,
  Signal, and Strategy identity at Candidate creation.
- [x] (2026-07-15) Milestone 4 - Added authorized shadow orchestration through the
  existing Strategy port, Portfolio, Risk, approved intent, dry-run Execution, and
  append-only decision records, with an injected Broker call probe.
- [ ] Milestone 5 - Documentation and final validation.

## Surprises & discoveries

- Observation: Live persistence has no migration ledger; its existing tables are
  created from an inline schema.
  Evidence: `swap_bot/decision_store.py` initializes `_SCHEMA` directly.
  Resolution: preserve that baseline and introduce the first numbered additive Live
  migration plus `live_schema_migrations`.
- Observation: there is a Strategy Protocol but no production Strategy implementation.
  Resolution: validate the port with a test-support Strategy in shadow; do not invent
  a production strategy in this plan.

## Decision log

- 2026-07-15: Treat Research assessment payloads as evidence selected explicitly by
  ID, never as automatic Live authority.
- 2026-07-15: Snapshot exact Research evidence during approval and prohibit Research
  DB access from runtime.
- 2026-07-15: Use exact nullable semantics and bounded validity; do not support
  wildcards or implicit latest-version behavior.
- 2026-07-15: Preserve the existing fixture characterization seam while requiring a
  stricter atomic Candidate append path for all validated-adoption traffic.

## Validation

Pending implementation. Final commands:

```powershell
python -m pytest -q
python -m ruff check .
python -m mypy packages/fx_core/src packages/fx_signal_store/src apps/fx_research/src apps/swap_bot/src
```

GitHub Actions must pass the existing Python 3.11 and 3.14 matrix.

Milestone 1 validation on Python 3.11:

- `25 passed` across evidence, adoption-decision, and adoption CLI tests.
- Ruff passed for the full repository.
- strict mypy passed for `packages apps` (61 source files).

Milestone 2 validation on Python 3.11:

- `36 passed` across evidence, decisions, CLI, and runtime adoption-gate tests.
- Runtime authorization succeeded while the Research database was exclusively locked,
  proving the gate reads Live state only.
- Ruff passed for the full repository; strict mypy passed for 62 source files.

Milestone 3 validation on Python 3.11:

- `23 passed` across Candidate authorization, runtime gate, and existing Live boundary
  tests.
- Missing, cross-Signal, cross-Strategy, and revoked stale authorizations left no
  partial Candidate rows.
- Candidate -> Portfolio -> Risk ID consistency remained intact.
- Ruff and strict mypy passed for the affected Live source.

Milestone 4 validation on Python 3.11:

- `17 passed` across the authorized adoption shadow cycle and existing shadow,
  Execution, Portfolio, and Risk tests.
- Explicit approval reached Candidate -> Portfolio ACCEPT -> Risk APPROVE -> approved
  intent -> `NOT_SUBMITTED`.
- The injected Counting BrokerGateway observed exactly zero submit calls.
- Research validation without Live approval stopped before Strategy; revocation
  stopped the next cycle before Strategy while preserving the historical cycle.
- Ruff and strict mypy passed for the affected Live source.
