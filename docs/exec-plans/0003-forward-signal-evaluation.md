# ExecPlan 0003: Forward Signal Evaluation

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

ExecPlan 0001 established the shared immutable Signal and lineage contracts. ExecPlan
0002 established operational News ingestion and Feature production. This ExecPlan adds
the first Research-only forward observation boundary. It does not decide whether a
Signal is useful; aggregate evaluation and validation remain later Program work.

## Goal

Given an immutable persisted Signal, deterministically schedule five forward horizons,
project supported targets onto `USD_JPY`, acquire complete OANDA v20 M1 midpoint
candles, calculate a versioned ForwardResult, and persist both the result and its exact
immutable market evidence append-only.

The completed path is:

```text
immutable Signal
    -> ForwardProjection
    -> ForwardObservationJob
    -> exact MarketSnapshot
    -> ForwardResult
```

The result must be reproducible without another network call. Provider failure,
alignment unavailability, and a legitimate zero return must be distinct states.

## Non-goals

- IC, hit rate, monotonicity, quantile analysis, MFE/MAE aggregation, or other metrics.
- Signal validation, promotion thresholds, Strategy adoption, or live trading changes.
- Changes to Signal scoring, horizons, Feature production, or the shared `fx_core`
  domain.
- Redesign or cleanup of ExecPlan 0002 ingestion and production code.
- A scheduler, daemon, backfill framework, or general batch orchestration system.
- Combining unrelated USD and JPY Currency Signals into a synthetic Pair Signal.
- Forward-filling missing market data or representing unavailable data as zero/neutral.

## Current state

- `fx_core.Signal` is immutable and persists `observed_at`, `created_at`, target,
  direction, source Feature IDs, and independent version metadata.
- `fx_signal_store.SQLiteSignalStore` exposes immutable Signal reads and lineage.
- `fx_research` owns operational ingestion state but has no market-data, forward-job,
  evidence, or ForwardResult contract.
- Research SQLite migrations `0001` and `0002` are already deployed and remain
  byte-for-byte unchanged.
- There is no Research dependency on Swap Bot and this plan will not introduce one.

## Target architecture and public contracts

```text
fx_core.Signal (read-only)
        |
        v
fx_research ForwardProjectionResolver
        |
        v
ForwardObservationJob ----> MarketDataSource port
        |                         |
        |                         v
        |                  OandaV20CandleSource
        v
ForwardCalculator ----> immutable MarketSnapshot
        |
        v
immutable ForwardResult ----> SQLiteForwardEvaluationStore
```

All new domain contracts are Research-only and live under `apps/fx_research`. Neither
`fx_core` nor Swap Bot imports them.

### Supported projection

The only initial instrument is `USD_JPY`:

| Signal target | Projection sign | Result |
|---|---:|---|
| Currency `USD` | `+1` | supported |
| Currency `JPY` | `-1` | supported |
| Pair `USD_JPY` | `+1` | supported |
| any other target | n/a | explicit unsupported projection |

The projection version is `currency-usdjpy-projection-v1`. Projection maps one
existing Signal to one market instrument; it never merges separate Currency Signals.
`CurrencyPairSignalTransformer` is not part of this path.

### Time and horizon semantics

`Signal.created_at` is the evaluation anchor because a Signal cannot be evaluated
before it exists. `Observation.first_seen_at` describes source availability upstream
and must not replace the later Signal-availability boundary.

Every supported Signal schedules exactly these horizons, irrespective of
`Signal.horizon`: `15m`, `1h`, `4h`, `1d`, and `3d`. The target time is
`signal.created_at + horizon`.

OANDA M1 timestamps are candle-open times. The entry price is the midpoint open of the
first complete candle whose open time is at or after the anchor. The exit price is the
midpoint open of the first complete candle whose open time is at or after the target.
Each alignment permits at most five minutes of delay, inclusive. A containing candle's
close is never substituted and missing candles are never forward-filled.

### Market evidence

`MarketCandle` is an immutable Research value carrying source, instrument,
granularity, price basis, open time, Decimal OHLC, completeness, and adapter/market
version. A provider correction at the same open time with changed content is a new
immutable candle revision.

`MarketSnapshot` stores the exact ordered candle revisions used from the aligned t0
through tx inclusive. Summary results reference a snapshot; they do not duplicate or
silently replace the evidence. Replaying a snapshot must not call the provider.

### Forward calculation

For projection sign `s`:

```text
raw_return = (price_tx / price_t0) - 1
target_return_bps = s * raw_return * 10000
```

Signal direction does not change target return. Signal strength and confidence are not
inputs to market outcome calculation.

For complete path candles with `t0 <= open_time < tx`, projected high/low returns are
computed from Decimal prices. Positive Signal direction uses the projected path;
negative direction negates it. MFE is `max(0, max(path))` and MAE is
`min(0, min(path))`. Both are null for neutral direction. The tx candle high and low
are excluded.

Realized volatility uses the ordered sequence `price_t0` followed by each path candle
close, computes log returns, and returns `sqrt(sum(r_i**2))`. It is a non-annualized
dimensionless float. All persisted prices, target return, MFE, and MAE use Decimal.
The formula version is `forward-result-v1`.

### Jobs and failures

Forward jobs are operational, mutable state separate from immutable results. States
are `PENDING`, `COMPLETED`, `FAILED`, and `UNAVAILABLE`; only `COMPLETED` references a
ForwardResult.

- Before `target_at + five minutes`, a job remains `PENDING`.
- Provider/transport/contract failure becomes `FAILED` with bounded sanitized error.
- A due job without a t0 candle becomes `UNAVAILABLE / T0_CANDLE_NOT_AVAILABLE`.
- A due job without a target candle becomes
  `UNAVAILABLE / TARGET_CANDLE_NOT_AVAILABLE`.
- Zero returns are completed numeric results, never failure sentinels.

ForwardResult semantic identity includes Signal ID, horizon, projection version,
formula version, and market source/instrument/granularity/price-basis/version semantics.
Duplicate one-shot execution cannot create a second result for the same identity.

## Invariants

- The immutable persisted Signal is read, never mutated or reinterpreted as a result.
- `signal.created_at`, not `observed_at` or source `first_seen_at`, anchors evaluation.
- No candle after the requested horizon/alignment window is consulted.
- Only complete candles participate in alignment, paths, or results.
- Missing or failed market observations never become zero returns.
- Projection sign and Signal direction have separate meanings.
- Exact market evidence is immutable and independently replayable.
- Provider credentials and raw error secrets are not persisted.
- Research-only types do not enter `fx_core` or Live packages.
- Existing Research migrations are not edited.

## Milestones

### Milestone 1 - Research contracts and deterministic projection

Contribution: fixes the Research-only language needed to observe a Signal without
changing the shared Signal contract.

Deliverables:

- Immutable `MarketCandle`, `MarketSnapshot`, `ForwardProjection`,
  `ForwardObservationJob`, and `ForwardResult` values.
- Explicit job status and unavailability reason enums.
- `MarketDataSource` application port.
- Fixed five-horizon schedule and projection resolver.
- Architecture tests proving no Research contracts leak into `fx_core` or Live.

Verification:

```powershell
python -m pytest -q tests/forward_domain tests/architecture
```

### Milestone 2 - Append-only evidence and result persistence

Contribution: makes market facts and calculated outcomes auditable independently from
operational retry state.

Deliverables:

- New numbered Research SQLite migration without modifying `0001` or `0002`.
- Immutable candle revisions and ordered snapshots.
- Append-only ForwardResult repository with semantic idempotency.
- Mutable operational job repository with bounded sanitized errors.
- Snapshot replay reads requiring no market provider.

Verification:

```powershell
python -m pytest -q tests/forward_persistence
```

### Milestone 3 - Versioned calculation and future-leakage checks

Contribution: turns exact evidence into a reproducible per-Signal outcome while fixing
alignment and horizon semantics before later metric work.

Deliverables:

- t0/tx complete-candle alignment with a five-minute maximum delay.
- Decimal target return and directional MFE/MAE.
- Non-annualized realized volatility.
- `forward-result-v1` calculation and evidence replay.
- Tests for direction/projection independence, neutral extrema, tx exclusion, missing
  alignment, and future-candle exclusion.

Verification:

```powershell
python -m pytest -q tests/forward_calculation
```

### Milestone 4 - OANDA adapter and one-shot observation

Contribution: proves the boundary against the initial operational data source without
introducing a scheduler or evaluation metrics.

Deliverables:

- OANDA v20 `USD_JPY` M1 midpoint, unsmoothed candle adapter.
- Environment-configured token, base URL, and timeout.
- Fake-transport and recorded-response contract tests.
- Explicit opt-in `oanda_smoke` test.
- `observe-forward-once --database ... --provider oanda --pair USD_JPY` CLI.
- Scheduling of five jobs per supported Signal and due-job-only observation.
- Idempotent rerun and failure/unavailability behavior.

Verification:

```powershell
python -m pytest -q tests/oanda_contract tests/forward_application
python -m fx_research observe-forward-once --database <path> --provider oanda --pair USD_JPY
```

## Migration and compatibility

- Add `0003_forward_signal_evaluation.sql` to the Research migration set.
- Do not alter shared Signal Store schema or existing Research migrations.
- Persist exact candle revisions instead of overwriting a timestamp row.
- Keep mutable job attempts separate from append-only evidence and results.
- OANDA credentials come only from environment variables and never enter fixtures,
  database records, logs, or errors.
- This plan creates no dependencies on `apps/swap_bot`.

## Validation and acceptance

Acceptance requires all of the following:

- One supported Signal deterministically creates five horizon jobs.
- USD, JPY, and USD_JPY projection signs are fixed; other targets fail explicitly.
- Entry and exit use the first complete M1 open at or after each boundary, within five
  minutes.
- Completed results preserve exact Decimal evidence and can be replayed offline.
- Direction affects MFE/MAE but never target return; neutral MFE/MAE are null.
- Duplicate rerun creates neither duplicate evidence links nor duplicate results.
- Signal records remain unchanged.
- Provider failure and candle unavailability are distinguishable and sanitized.
- OANDA incomplete candles are never used.
- Full tests, Ruff, and strict mypy pass on the Python 3.11 baseline.

Final commands:

```powershell
python -m pytest -q
python -m ruff check .
python -m mypy packages/fx_core/src packages/fx_signal_store/src apps/fx_research/src apps/swap_bot/src
```

Validation evidence recorded at completion will include Python version, test counts,
lint result, mypy result, OANDA smoke result or explicit credential reason, five-job
count, completed result count, duplicate rerun behavior, Signal immutability check, and
offline evidence replay.

## Progress

- [x] (2026-07-14) Read repository instructions, Program/architecture/data/test design,
  ExecPlan 0002, and the Research Evaluation and Architecture Change skills.
- [x] (2026-07-14) Confirmed current `main` is clean and synchronized with
  `origin/main` at `cc0c551`.
- [x] (2026-07-14) Confirmed OANDA v20 official OpenAPI candle request and response
  fields.
- [x] (2026-07-14) Created this living ExecPlan before implementation.
- [ ] Milestone 1 - Research contracts and deterministic projection.
- [ ] Milestone 2 - Append-only evidence and result persistence.
- [ ] Milestone 3 - Versioned calculation and future-leakage checks.
- [ ] Milestone 4 - OANDA adapter and one-shot observation.
- [ ] Full validation and handoff.

## Surprises & Discoveries

- The public documentation page currently did not provide a usable response from this
  environment. The authoritative OANDA `v20-openapi` repository remained available;
  its specification confirms `/v3/instruments/{instrument}/candles`, midpoint price
  component `M`, M1 granularity, unsmoothed candles, open timestamps, completeness, and
  decimal-string OHLC fields.

## Decision log

- 2026-07-14: Anchor forward observation at `Signal.created_at`. A source may be known
  at `first_seen_at`, but the scored Signal did not yet exist and using the earlier time
  would introduce unavailable-to-the-consumer history.
- 2026-07-14: Project one existing Signal at a time. Unrelated USD and JPY Signals are
  not paired because doing so would invent a new Signal identity, creation time, and
  lineage outside the immutable Signal contract.
- 2026-07-14: Store exact market evidence separately from ForwardResult summaries.
  Provider revisions and calculation replay must remain auditable without rewriting a
  previously computed result.
- 2026-07-14: Keep Research evaluation contracts in `fx_research`; they are downstream
  consumers and are not shared trading domain concepts.

## Validation

Implementation validation is pending.
