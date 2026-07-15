# ExecPlan 0004: Signal Validation Framework

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

ExecPlan 0001 established the shared immutable Signal language. ExecPlan 0002
operationalized News ingestion and Feature production. ExecPlan 0003 stored exact
market evidence and completed ForwardResult records. This ExecPlan aggregates those
Research outcomes without changing Signal or enabling Strategy adoption. Validated
Signal use in Live remains ExecPlan 0005.

## Goal

Create a reproducible Evaluation Dataset from immutable Signal records and completed
ForwardResult records, segment it into strict semantic cohorts, calculate versioned
Research metrics, and persist EvaluationReport plus optional ValidationAssessment
records append-only.

```text
Signal + ForwardResult
        -> EvaluationSample
        -> Strict Evaluation Cohort
        -> Metrics
        -> EvaluationReport
        -> Optional ValidationAssessment
```

Repeated execution over the same full captured input snapshot and configuration reuses
one Evaluation Run. Newly completed ForwardResult records or changed non-completed job
states produce a new run. Statistical undefined states and insufficient samples remain
explicit data, not application failures.

## Non-goals

- Pearson IC, Live PnL labeling, Strategy backtests, or generic analytics queries.
- Signal, Feature, ForwardResult, MarketSnapshot, projection, or forward-formula changes.
- TradeCandidate, Portfolio, Risk, Execution, Broker, or Live package changes.
- `APPROVED_FOR_STRATEGY`, automatic Live adoption, or production promotion thresholds.
- Scheduler, daemon, dashboard, or implicit cross-version aggregation.
- Dataset-derived volatility regime boundaries without a versioned immutable policy.
- Recomputing or overwriting a Signal score.

## Current state

- HEAD `d7cad8f7b417d7d1addf7bf6793774cdede90fdd` is clean and synchronized with
  `origin/main` at plan creation.
- `fx_core.Signal` is a frozen ex-ante record with target, signal type, direction,
  Signal horizon, timestamps, lineage IDs, and producer/model/prompt/scorer/
  transformation versions.
- `fx_signal_store.SQLiteSignalStore` owns shared immutable Signal reads.
- `fx_research.ForwardResult` is frozen and carries a distinct Forward horizon,
  market semantics, projection/formula versions, result values, completion time, and
  immutable snapshot identity.
- `SQLiteForwardEvaluationStore` owns Research migrations 0001-0003, operational
  Forward jobs, immutable market evidence, and append-only ForwardResult records.
- The Research CLI has collection, production, and forward-observation one-shot
  commands, but no Evaluation Dataset, metric, report, assessment, or evaluation CLI.
- Research imports no Swap Bot module. Evaluation-specific contracts do not belong in
  `fx_core` or `fx_signal_store`.

## Target architecture

```text
shared Signal tables (read-only) ----+
                                     |
Research Forward jobs/results -------+--> SQLiteEvaluationStore.capture_inputs()
                                               |
                                               v
                                      EvaluationInputSnapshot
                                               |
                                               v
                                      Strict Cohort Grouper
                                               |
                                               v
                                      Versioned Metric Evaluator
                                               |
                                               v
                                      append-only Evaluation Run/Report
                                               |
                                 explicit policy only
                                               v
                                      ValidationAssessment
```

New contracts and persistence remain under `apps/fx_research`. `fx_core`,
`fx_signal_store`, and `apps/swap_bot` do not import them.

Expected modules:

- `fx_research/evaluation.py`: immutable samples, cohort identity, report,
  diagnostics, configuration, policy, and assessment values.
- `fx_research/evaluation_metrics.py`: pure Spearman, bootstrap, Hit Rate, bucket,
  MFE/MAE, and quarterly-slice calculations.
- `fx_research/evaluation_persistence.py`: one-read input capture and append-only
  SQLite run/report/policy/assessment repository.
- `fx_research/evaluation_application.py`: one-shot grouping, evaluation,
  idempotency, and optional assessment orchestration.
- `fx_research/migrations/0004_signal_validation_framework.sql`: new schema only.
- `fx_research/migrations/0005_evaluation_input_snapshot.sql`: append-only full input
  snapshot evidence added without editing migration 0004.
- `fx_research/__main__.py`: `evaluate-signals-once` command.

### Evaluation input snapshot

One SQLite read transaction captures all relevant Signal, Forward job, and completed
ForwardResult rows into an immutable in-memory snapshot before calculation begins.
Later database writes cannot alter that run's input set.

Only a completed numeric ForwardResult becomes an EvaluationSample. `PENDING`,
`FAILED`, and `UNAVAILABLE` jobs remain exclusion diagnostics and never become zero
return samples. Their job IDs, captured statuses, reasons, and strict cohort identities
are part of the immutable run snapshot. Unsupported Signal IDs and incomplete-horizon
Signal IDs are also persisted as formal snapshot evidence. Neutral direction, zero
return, and missing MFE/MAE remain included samples with metric-specific denominator/
null diagnostics.

The initial score definition is fixed:

```text
score_definition_version = signal-direction-v1
score = Signal.direction.value
```

Strength and confidence are not multiplied into this score. Pair scores outside the
fixed `[-1, 1]` bucket range remain valid IC/Hit Rate samples but are reported as
metric-specific unbucketed observations; they are never clamped.

### Strict cohort identity

`strict-evaluation-cohort-v1` contains all of:

- signal type, target type, and target value;
- Signal horizon and Forward horizon as separate dimensions;
- producer, model, prompt, scorer, and nullable transformation versions;
- market source, market-data version, price basis, and granularity;
- projection version and formula version;
- score-definition version.

No dimension is dropped from the identity hash. GMO FX BID and OANDA midpoint cannot
share a cohort. Different model, prompt, scorer, projection, or formula versions cannot
share a cohort. Combined analysis requires a future explicit analysis version and is
not implemented here.

### Metrics and explicit states

`research-metrics-v1` computes:

- Spearman IC using average ranks for ties. Fewer than three samples, constant score,
  and constant return produce distinct undefined reasons. A deterministic paired
  bootstrap stores version, seed, iteration count, and a 95% interval.
- Hit Rate over non-neutral and non-zero-return samples, including total, eligible,
  hit, neutral, and zero-return counts plus a Wilson 95% interval.
- Five fixed score buckets with count, mean, median, deterministic mean interval, and
  explicit empty state. Monotonicity stores non-empty count, non-decreasing adjacent
  step count/ratio, and nullable Spearman correlation of bucket order with means.
- Separate MFE and MAE summaries with eligible/null counts, mean, median, and fixed
  lower/upper quantiles. Null values never become zero.
- Calendar-quarter slices with sample count and Spearman IC. A slice with fewer than
  three samples records `INSUFFICIENT_SAMPLE`; it is not a failed run.
- Sample diagnostics with total/included/excluded counts, exclusion reasons, and first/
  last Signal creation and ForwardResult completion times.

Initial bootstrap defaults are an immutable `paired-bootstrap-v1` configuration with
an explicit seed and iteration count. Bucket boundaries and quantiles live in the
metric configuration snapshot and contribute to run identity.

### Evaluation and assessment identity

An Evaluation Run identity is the deterministic digest of:

- the ordered `(Signal ID, ForwardResult ID)` input pairs;
- non-completed job IDs, captured statuses, reasons, and exclusion cohort identities;
- unsupported and incomplete-horizon Signal IDs;
- evaluator version;
- score-definition version;
- strict grouping configuration;
- metric and bootstrap configuration.

One run contains one report per strict cohort. Exact ordered input IDs are persisted
with ordinals, and the full captured snapshot is persisted as canonical append-only
evidence. Repeating the same identity returns the existing run and reports. Adding a
completed ForwardResult or changing a captured non-completed job status changes the
run ID.

EvaluationReport contains cohort identity, metrics, confidence intervals, explicit
undefined/insufficient reasons, diagnostics, and creation time. JSON payloads are
canonical and versioned; database identity and input links remain relational.

ValidationAssessment is separate from EvaluationReport. It requires an explicit
immutable JSON policy with a policy version and configurable sample, Spearman point/CI,
Hit Rate point/CI, bucket coverage, monotonicity, and stability-slice conditions.
The evaluator may emit only `EXPERIMENTAL`, `PROMISING`, or
`VALIDATED_FOR_RESEARCH`. It cannot emit `APPROVED_FOR_STRATEGY` or automatically emit
`DEPRECATED`.

The persistence boundary revalidates that every captured sample belongs to the report
cohort, appears exactly once across reports, and is not omitted. Assessment persistence
also verifies the Report belongs to the referenced run, the persisted policy hash
matches, and the recomputed cohort and metric payloads equal the persisted Report.

## Invariants

- Signal, ForwardResult, and MarketSnapshot records remain byte-for-byte unchanged.
- Research evaluation never imports or invokes Strategy, Portfolio, Risk, Execution,
  Broker, or any Swap Bot module.
- Evaluation-specific types remain outside `fx_core` and `fx_signal_store`.
- Signal direction is read as the versioned score; it is never recomputed or written.
- Signal horizon and Forward horizon remain distinct cohort dimensions.
- Different market/version semantics never enter one headline cohort implicitly.
- Non-completed jobs never become EvaluationSample or zero return.
- Zero return, neutral direction, null excursion, undefined metric, insufficient
  sample, and application failure remain distinct states.
- Every sliced metric includes its sample count.
- Evaluation input IDs are fixed before metric calculation begins.
- Evaluation runs, input links, reports, policies, and assessments are append-only.
- Existing migrations 0001, 0002, 0003, and 0004 are not edited.
- UTC-aware timestamps and deterministic configuration/version identities are required.

## Milestones

### Milestone 1 - Evaluation Dataset and strict cohort contracts

Contribution: fixes the Research-only language that prevents semantic versions and
market bases from being silently combined.

Deliverables:

- Frozen EvaluationSample, CohortIdentity, input snapshot, diagnostics, and versioned
  configuration values.
- `signal-direction-v1` score extraction without Signal mutation.
- Strict cohort grouping and identity.
- Explicit completed/included and PENDING/FAILED/UNAVAILABLE exclusion states.
- Architecture tests preserving Research/Live and shared-domain boundaries.

Expected changes: `evaluation.py`, `tests/evaluation_domain`, and architecture tests.

Observable behavior:

- Different scorer/model/prompt/formula/projection/market semantics form different
  cohorts.
- Signal and Forward horizons are independently represented.
- Non-completed jobs are not samples, and source records compare equal before/after.

Verification:

```powershell
python -m pytest -q tests/evaluation_domain tests/architecture
```

### Milestone 2 - Deterministic Research metrics and stability

Contribution: provides reproducible evidence for Signal information content without
introducing promotion policy.

Deliverables:

- Average-rank Spearman and deterministic paired bootstrap interval.
- Hit Rate denominator diagnostics and Wilson interval.
- Fixed score buckets, explicit empty/unbucketed state, and monotonicity diagnostics.
- Separate MFE/MAE summaries.
- Calendar-quarter slices and explicit insufficient-sample state.
- Hand-calculated deterministic tests.

Expected changes: `evaluation_metrics.py` and `tests/evaluation_metrics`.

Observable behavior:

- Perfect/inverse/tied Spearman values match hand calculation.
- Degenerate IC is undefined rather than zero.
- Identical bootstrap input/configuration produces an identical interval.
- Neutral/zero-return/null excursion exclusions are counted.

Verification:

```powershell
python -m pytest -q tests/evaluation_metrics
```

### Milestone 3 - Consistent input capture and append-only persistence

Contribution: makes exact inputs, semantic cohorts, metrics, and replay identity
auditable without changing upstream records.

Deliverables:

- Migration `0004_signal_validation_framework.sql` without changing 0001-0003.
- Migration `0005_evaluation_input_snapshot.sql` without changing 0004.
- Single-read input capture from Signal/Forward records.
- Append-only run, ordered input, report, policy, and assessment tables.
- Deterministic run idempotency and replay reads.
- UPDATE/DELETE rejection triggers for every immutable Evaluation table.

Expected changes: migration, `evaluation_persistence.py`, and
`tests/evaluation_persistence`.

Observable behavior:

- Identical ordered inputs/configuration reuse a run.
- A newly completed ForwardResult produces a distinct run.
- Exact input IDs and reports replay without reading current/latest versions.
- Evaluation records reject UPDATE and DELETE.

Verification:

```powershell
python -m pytest -q tests/evaluation_persistence
```

### Milestone 4 - Optional policy assessment and one-shot CLI

Contribution: proves an operational Research evaluation cycle while keeping
statistical reporting separate from promotion authority.

Deliverables:

- Versioned ValidationPolicy parser and immutable persistence.
- Assessment logic limited to EXPERIMENTAL/PROMISING/VALIDATED_FOR_RESEARCH.
- `EvaluateSignalsOnceService` with report reuse and per-cohort failure accounting.
- `evaluate-signals-once --database ... [--validation-policy ...]`.
- JSON summary with scanned results, samples, cohorts, created/reused reports,
  assessments, undefined/insufficient metrics, and failures.

Expected changes: `evaluation_application.py`, `__main__.py`, CLI/assessment tests,
and permanent Research/data/test design documentation.

Observable behavior:

- No policy creates reports only; an explicit policy may create assessments.
- Invalid policy or processing failures return non-zero; undefined metrics and
  insufficient samples do not.
- No assessment can produce `APPROVED_FOR_STRATEGY`.
- Strategy/Broker invocation probes remain at zero.

Verification:

```powershell
python -m pytest -q tests/evaluation_application tests/evaluation_cli
python -m fx_research evaluate-signals-once --database <path>
```

### Milestone 5 - Program validation and reproducibility audit

Contribution: demonstrates that the complete Research path remains reproducible on
supported Python versions without crossing into ExecPlan 0005.

Deliverables:

- Full regression, lint, and strict type checks on Python 3.11 and 3.14.
- Validation evidence for metric hand calculations, version separation, idempotency,
  append-only storage, immutable upstream records, and zero Live/Broker calls.
- Updated `docs/03_SIGNAL_AND_RESEARCH.md`, `docs/05_DATA_AND_VERSIONING.md`, and
  `docs/08_TEST_STRATEGY.md` for permanent contracts.
- Completed living Progress, Decision Log, and Validation sections.

Verification:

```powershell
python -m pytest -q
python -m ruff check .
python -m mypy packages/fx_core/src packages/fx_signal_store/src apps/fx_research/src apps/swap_bot/src
```

## Migration and compatibility

- Preserve migrations 0001-0004. Add migration 0005 for immutable full-snapshot
  evidence; do not rewrite existing Evaluation rows.
- Keep evaluation data in the same SQLite file so one read transaction can freeze
  Signal and ForwardResult inputs; do not move Research statistics into the shared
  Signal store.
- Existing databases migrate forward automatically. No existing rows are rewritten.
- Exact upstream IDs are references, not copied mutable Signal/ForwardResult payloads.
- Canonical versioned JSON stores metric/configuration snapshots while relational links
  preserve input ordering and cohort/report identity.
- No external credential, market network call, Strategy invocation, or Broker call is
  part of evaluation.
- CLI defaults to report-only behavior. Assessment requires an explicitly supplied
  policy file.

## Validation and acceptance

Acceptance requires all of the following:

- Completed Signal/ForwardResult pairs form reproducible EvaluationSample records.
- Strict cohorts contain every required Signal, model, market, projection, formula,
  horizon, and score-definition dimension.
- GMO BID and OANDA midpoint are separated.
- Spearman, deterministic bootstrap, Hit Rate, fixed buckets, MFE, MAE, diagnostics,
  and quarterly stability meet their explicit contracts.
- Undefined/insufficient/missing states are persisted and never replaced by zero.
- Exact full input snapshot, configuration, reports, and optional assessments replay.
- Identical full snapshots are idempotent; newly completed results or changed captured
  job states create a new run.
- Report persistence rejects cross-cohort, duplicate, missing, or out-of-snapshot
  inputs before any partial run is written.
- Assessment persistence rejects cross-run Reports, policy hash mismatches, and
  recomputed cohort/metric payload mismatches before any assessment is written.
- All evaluation records reject update/delete.
- Validation policy is required for assessment and cannot approve Strategy use.
- Signal and ForwardResult remain unchanged.
- Research imports and invokes no Live/Strategy/Broker code.
- Full tests, Ruff, and strict mypy pass on Python 3.11 and 3.14.

## Progress

- [x] (2026-07-14) Read root/Research instructions, Program/architecture/domain/data/
  test design, relevant ADRs, ExecPlan 0003, and Research Evaluation/Architecture
  Change skills.
- [x] (2026-07-14) Confirmed clean synchronized baseline at `d7cad8f` and mapped
  current Signal, ForwardResult, Research migration, persistence, and CLI boundaries.
- [x] (2026-07-14) Created this living ExecPlan before implementation.
- [x] (2026-07-14) Milestone 1 - Added immutable Evaluation samples/configuration,
  exact strict cohort identity, direction-only score extraction, deterministic grouping,
  and explicit non-completed exclusion types.
- [x] (2026-07-14) Milestone 2 - Added average-rank Spearman, deterministic paired
  bootstrap, Hit Rate/Wilson diagnostics, fixed buckets/monotonicity, excursion
  summaries, quarterly slices, and hand-calculated tests.
- [x] (2026-07-14) Milestone 3 - Added migration 0004, single-transaction input
  capture, deterministic run/report identities, ordered input replay, immutable policy
  snapshots, append-only storage, and update/delete rejection.
- [x] (2026-07-14) Milestone 4 - Added report-only one-shot evaluation, explicit JSON
  policy parsing, policy-driven assessments limited to Research statuses, idempotent
  assessment persistence, JSON summary, failure exit behavior, and Live import guards.
- [x] (2026-07-14) Milestone 5 - Updated permanent Research/data/test contracts and
  passed the full pytest, Ruff, and strict mypy suite on Python 3.11.9 and 3.14.6.
- [x] (2026-07-15) Review correction - Expanded run identity and append-only replay to
  the full captured diagnostic snapshot, added report/sample cohort membership checks,
  enforced Assessment Run/Report/Policy/payload lineage, and exposed unsupported and
  incomplete-horizon counts in the CLI result.

## Surprises & Discoveries

- A Pair Signal direction may occupy `[-2, 2]`, while the requested initial buckets
  cover `[-1, 1]`. Clamping would change score semantics, so out-of-range Pair scores
  remain visible as metric-specific unbucketed counts.
- Shared Signal and Research Forward records already coexist in one SQLite file. A
  Research-owned single read transaction can therefore freeze exact evaluation inputs
  without moving Evaluation types into `fx_core` or adding a distributed snapshot.
- Python 3.14 was not initially present in the local environment. The official
  `Python.Python.3.14` user-scoped package supplied Python 3.14.6, allowing the same
  CI commands to run locally rather than inferring compatibility from Python 3.13.
- Completed input IDs alone did not identify exclusion diagnostics because Forward job
  status is mutable operational state. A PENDING job becoming FAILED or UNAVAILABLE can
  change captured evidence without changing any completed Result ID.
- SQLite foreign keys could prove that a Run, Report, and Policy each existed, but not
  that the Report belonged to the Assessment Run or that the referenced policy hash
  matched the immutable policy content.
- The Python 3.14 mypy binary wheel was blocked by local Windows application control.
  Installing the same mypy release from source produced a pure-Python wheel and allowed
  the required 3.14 type check to run successfully.

## Decision log

- 2026-07-14: Keep Evaluation contracts and persistence in `fx_research`; Research
  consumes shared Signal but its statistical outputs are not shared trading domain.
- 2026-07-14: Use one global run identity for the ordered completed input set and one
  report per strict cohort. This makes newly completed results create a new auditable
  run without combining cohort semantics.
- 2026-07-14: Use `Signal.direction.value` only for `signal-direction-v1`. Strength and
  confidence combinations require future score-definition versions.
- 2026-07-14: Treat non-completed Forward jobs as diagnostics, not samples. Zero return,
  neutral direction, and null MFE/MAE remain completed samples with metric-specific
  denominator/null handling.
- 2026-07-14: Use fixed calendar quarters from `Signal.created_at`, the ex-ante record
  availability boundary. Forward completion time remains a report diagnostic rather
  than deciding the historical slice containing the Signal.
- 2026-07-14: Capture Signal, Forward job, and ForwardResult rows through one SQLite
  read transaction, then calculate from that immutable in-memory snapshot. Holding a
  write transaction across bootstrap calculation would add unnecessary contention;
  exact ordered IDs provide the stable persistence boundary instead.
- 2026-07-14: Map policy outcomes to `EXPERIMENTAL`, `PROMISING`, and
  `VALIDATED_FOR_RESEARCH` only. Point estimates may identify PROMISING evidence, while
  every configured point, confidence, bucket, monotonicity, and stability condition
  must pass for VALIDATED_FOR_RESEARCH; neither status grants Strategy authority.
- 2026-07-14: Do not create a new ADR at plan creation. The design follows accepted
  Research/Live sibling, immutable Signal, shared SQLite boundary, and market semantic
  separation decisions rather than changing them.
- 2026-07-15: Define `evaluation-input-snapshot-v2` over ordered completed IDs,
  non-completed job/status/reason/cohort evidence, unsupported/incomplete Signal IDs,
  and captured counts. Persist it through migration 0005 instead of modifying the
  already-applied migration 0004.
- 2026-07-15: Revalidate report membership at the persistence boundary rather than
  trusting the evaluator's flattened IDs. Each captured sample must match the report
  cohort and appear exactly once before the transaction writes a Run.
- 2026-07-15: Require Assessment persistence to compare Report run ownership, policy
  content hash, and recomputed cohort/metric payloads. Separate valid foreign-key
  references do not by themselves establish this lineage.

## Validation

Record at completion:

- Python 3.11 and 3.14 versions and full pytest passed/skipped counts.
- Ruff and strict mypy results.
- Hand-calculated positive, inverse, and tied Spearman evidence.
- Hit Rate denominator and Wilson interval evidence.
- Bucket boundary, empty bucket, monotonic/non-monotonic evidence.
- Deterministic bootstrap replay evidence.
- Strict scorer/model/prompt/formula/projection and GMO/OANDA separation evidence.
- Duplicate/new-input run identities and immutable Evaluation storage evidence.
- Signal/ForwardResult before/after equality and zero Strategy/Broker invocation.

Milestone 1/2 focused validation on 2026-07-14:

- Evaluation domain, metrics, and architecture: 28 passed.
- Ruff: all checks passed for new production and test modules.
- Strict mypy: 2 new source files checked, no issues.
- Hand-calculated Spearman: positive `1.0`, inverse `-1.0`, tied
  `0.9486832980505138`; insufficient and constant inputs remained undefined.
- Hit Rate fixture: 5 total, 3 eligible, 2 hits, 1 neutral, 1 zero return, with Wilson
  interval matching the hand-calculated fixture.
- Fixed bucket boundaries produced counts `(1, 1, 1, 1, 2)`; explicit empty,
  monotonic, non-monotonic, and out-of-range Pair score states passed.
- Identical bootstrap input/configuration produced identical intervals.
- Scorer/model/prompt/transformation, projection/formula, GMO BID/OANDA midpoint, and
  Signal/Forward horizons produced separate cohort identities.
- Original frozen Signal and ForwardResult values remained equal before and after sample
  extraction.

Milestone 3 focused validation on 2026-07-14:

- Evaluation persistence plus Forward migration regression: 15 passed.
- Ruff: all checks passed for new persistence and tests.
- Strict mypy: new persistence module checked, no issues.
- One completed result became one sample while PENDING, FAILED, and UNAVAILABLE jobs
  remained explicit exclusions.
- Identical ordered inputs/configuration reused the same run and report; a second
  completed ForwardResult created a distinct run while the first run replayed its
  original exact input IDs.
- Canonical cohort/metric payloads replayed with separate Signal and Forward horizons.
- Evaluation run/report update and delete attempts failed through immutable triggers.
- Reusing one policy version with different content was rejected.

Milestone 4 focused validation on 2026-07-14:

- Evaluation application, CLI, and architecture boundary: 15 passed.
- Ruff: all checks passed for application, CLI, integration support, and tests.
- Strict mypy: application and CLI modules checked, no issues.
- Report-only execution created no assessment. Explicit policy execution created one
  policy-referenced assessment, and identical rerun reused the report and assessment.
- Persisting an assessment before its policy failed its foreign-key contract.
- Validation status values exclude `APPROVED_FOR_STRATEGY`.
- Synthetic metric processing failure produced `failed=1`, no partial Evaluation Run,
  and CLI non-zero behavior.
- Architecture checks found no evaluation import of Strategy, Portfolio, Risk,
  Execution, Broker, or Swap Bot modules.

Final validation on 2026-07-14:

- Python 3.11.9: 192 passed, 5 opt-in external smoke tests skipped, 0 failed; Ruff all
  checks passed; strict mypy checked 54 source files with no issues.
- Python 3.14.6: 192 passed, 5 opt-in external smoke tests skipped, 0 failed; Ruff all
  checks passed; strict mypy checked 54 source files with no issues.
- Hand-calculated Spearman results were positive `1.0`, inverse `-1.0`, and tied
  `0.9486832980505138`. Constant score/return and fewer than three samples persisted
  explicit undefined reasons.
- Hit Rate fixture retained 5 total, 3 eligible, 2 hits, 1 neutral, and 1 zero-return
  observation with the expected Wilson 95% interval.
- Fixed bucket boundaries, explicit empty state, full/non-full monotonicity, and
  unbucketed out-of-range Pair scores passed. Null MFE/MAE remained null and counted.
- Deterministic bootstrap replay returned identical intervals from identical input,
  seed, iteration count, and version.
- Different scorer/model/prompt/transformation, projection/formula, Signal/Forward
  horizon, and GMO BID/OANDA midpoint semantics produced different cohort identities.
- Identical ordered inputs/configuration reused one Evaluation Run. A new completed
  ForwardResult produced a new run while the earlier exact input IDs remained replayable.
- UPDATE/DELETE attempts against immutable Evaluation records failed. A policy version
  could not be reused with changed content, and Assessment required its persisted policy.
- Signal and ForwardResult before/after equality passed. Evaluation modules import and
  invoke no Strategy, Portfolio, Risk, Execution, Broker, or Swap Bot path.

Review-correction validation on 2026-07-15:

- Focused Evaluation domain/persistence/application/CLI and Forward migration tests:
  43 passed.
- Python 3.11.9: 201 passed, 5 opt-in external smoke tests skipped, 0 failed; Ruff all
  checks passed; strict mypy checked 54 source files with no issues.
- Python 3.14.6: 201 passed, 5 opt-in external smoke tests skipped, 0 failed; Ruff all
  checks passed; strict mypy checked 54 source files with no issues. The local Windows
  policy required the mypy 2.3.0 source-built pure-Python wheel.
- Identical full snapshots reused one run. PENDING-to-FAILED and separate
  PENDING-to-UNAVAILABLE transitions produced distinct runs while completed Result IDs
  remained unchanged.
- Persisted snapshot replay retained captured non-completed job status/reason/cohort,
  unsupported Signal IDs, incomplete-horizon Signal IDs, and completed identities.
  Exclusions remained outside Evaluation samples and were not converted to zero.
- Cross-cohort input assignment, duplicate input assignment, and missing input coverage
  were rejected before any Run or Report row was written.
- Cross-run Report references, wrong policy content hashes, and reused-run recomputed
  cohort/metric mismatches were rejected before any Assessment row was written. A valid
  identical rerun continued to reuse its Report and Assessment.
