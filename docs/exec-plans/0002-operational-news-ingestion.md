# ExecPlan 0002: Operational News Ingestion and Feature Production

## Goal

Connect the selected Federal Reserve and Bank of Japan sources to a repeatable Research-owned
application path:

```text
Official source -> collected item -> NewsObservation
    -> CurrencyFundamentalFeature -> immutable Signal -> fx_signal_store
```

The completed path must be safe to run repeatedly, preserve the first time content became
available to the collector, retain producer/model/prompt/scorer versions, and never import or
invoke Live Strategy, Portfolio, Risk, or Execution.

## Non-goals

- Forward Result, IC, Hit Rate, monotonicity, MFE/MAE, or Signal validation
- Strategy adoption, Live decisions, or Broker submission
- GDELT, Banxico, ECB, Bank of England, NHK, yfinance, NewsAPI, Reuters, or Bloomberg
- Cross-source duplicate merging or historical Signal backfill
- Migrating or cleaning legacy NewsScoring or Swap Bot code
- A daemon, distributed queue, generic scheduler, or generic retry framework

## Current state

ExecPlan 0001 is complete. `fx_core` defines immutable `NewsObservation`,
`CurrencyFundamentalFeature`, `LlmFeatureExtractor`, and `Signal` contracts.
`fx_signal_store` provides append-only SQLite Observation/Feature/Signal lineage. Its current
append methods reject duplicate IDs and expose no operational polling state.

`apps/fx_research` contains only local repository instructions. The recorded feature provider in
`swap_bot` cannot be imported because Research and Live are sibling applications. No operational
News source, normalization boundary, retrying HTTP GET adapter, ingestion evidence store, or
Research CLI exists.

Official sources confirmed on 2026-07-13:

| Source ID | Candidate | Adapter | Official endpoint |
|---|---|---|---|
| `fed.press_monetary.rss` | USD | RSS | `https://www.federalreserve.gov/feeds/press_monetary.xml` |
| `fed.speeches.rss` | USD | RSS | `https://www.federalreserve.gov/feeds/speeches.xml` |
| `boj.monetary_policy.html` | JPY | HTML listing | `https://www.boj.or.jp/en/mopo/mpmdeci/mpr_all/index.htm` and current-year listing |
| `boj.speeches.html` | JPY | HTML listing | `https://www.boj.or.jp/en/mopo/r_menu_koen/index.htm` |

The Fed documents the two RSS feed families. BOJ exposes HTML listings; its English RSS link was
not usable during investigation. BOJ monetary-policy detail links are commonly PDFs, so a listing
item and a successfully extracted detail body are separate operational outcomes.

## Target architecture

```text
apps/fx_research
  infrastructure/federal_reserve  -- RSS and detail adapters
  infrastructure/bank_of_japan    -- HTML listing and detail adapters
  infrastructure/http             -- bounded GET retry and timeout
  infrastructure/persistence      -- fetch evidence and production state
  collection                      -- NewsSource and collected-item contracts
  normalization                   -- canonical text/URL and deterministic identity
  feature_production              -- LlmFeatureExtractor + fundamental-scorer-v1
  application                     -- collect-once and produce-signals-once
              |                         |
              +----> fx_core <----------+
              +----> fx_signal_store

swap_bot  <X>  fx_research
```

Source-specific feed entries, HTML elements, HTTP responses, and PDF parser objects remain inside
Research infrastructure. Candidate currency comes from immutable source configuration: Fed is USD
and BOJ is JPY. The LLM never selects a currency or produces action/order semantics.

### Application contracts

- `NewsSource.fetch() -> tuple[CollectedNewsItem, ...]`
- `NewsNormalizer.normalize(item, first_seen_at) -> NewsObservation`
- `LlmFeatureExtractor.extract(observation, feature_id, currency) -> CurrencyFundamentalFeature`
- `FundamentalSignalScorer.score(...) -> Signal`
- `IngestionStateRepository` records fetch evidence and production state without entering
  `fx_core`.

`CollectedNewsItem` is a Research application type containing source identity, candidate currency,
canonical detail URL, title, extracted textual body, optional exact published timestamp, optional
raw source date text, and immutable payload evidence. It contains no score or trade meaning.

## Invariants

- `first_seen_at` is the first successful recognition time of a normalized content version.
- Missing exact publication time remains `published_at=None`; date-only metadata never becomes
  fabricated midnight.
- Observation identity is deterministic over source ID, canonical payload URL, and normalized
  content hash.
- Same URL and same normalized content is idempotent across process runs. Changed content at the
  same URL creates a new immutable Observation.
- Cross-source duplicate merging is not performed.
- Empty, malformed, or structurally unrecognized content produces an explicit failure, not a
  neutral Feature or Signal.
- Retry applies only to bounded HTTP GET failures selected by policy. A failed fetch creates no
  Observation, Feature, or Signal.
- Feature versions retain producer/model/prompt dimensions; Signal uses
  `fundamental-scorer-v1` without semantic changes.
- `fx_research` and `swap_bot` never import each other.
- No production or smoke path calls Strategy, Portfolio, Risk, Execution, or Broker code.

## Milestones

### Milestone 1 — Research application and recorded source contracts

Contribution: establish provider-specific collection seams without changing shared domain meaning.

Deliverables:

- Python `>=3.11` `fx_research` package and root tooling registration
- Research-only `CollectedNewsItem`, `NewsSource`, HTTP response, and source configuration types
- Fed RSS parsers for monetary-policy releases and speeches as separate source IDs
- BOJ HTML listing parsers for monetary-policy releases and monetary-policy speeches
- recorded RSS/HTML/detail fixtures
- explicit malformed-feed and changed-HTML errors
- architecture tests for Research/Live sibling isolation

Observable behavior:

- Fed items carry USD from configuration; BOJ items carry JPY.
- Source IDs remain distinct even when organizations match.
- Source parser objects do not cross the application boundary.
- Missing or empty expected listing content fails explicitly.

Verification:

```powershell
python -m pytest -q tests/research_collection tests/architecture
python -m ruff check .
python -m mypy packages apps
```

### Milestone 2 — Deterministic normalization and operational idempotency

Contribution: make repeated polling reproducible while preserving ex-ante availability time.

Deliverables:

- Unicode, whitespace, URL, title, and body canonicalization
- deterministic Observation/content identifiers
- idempotent append operations in `fx_signal_store` without rewriting migration 0001
- Research-owned numbered SQLite migration for fetch evidence and production state
- one fetch evidence record for success/failure with source date text where available
- collection service that preserves the original `first_seen_at`

Observable behavior:

- repeated identical polls create one Observation.
- changed content at the same canonical URL creates another Observation.
- repeated polls do not move `first_seen_at` forward.
- BOJ date-only text is retained in evidence while `published_at` stays `None`.

Verification:

```powershell
python -m pytest -q tests/research_normalization tests/research_persistence
python -m ruff check .
python -m mypy packages apps
```

### Milestone 3 — Versioned Feature and Signal production

Contribution: operationally connect collected Observations to the existing shared Feature/Signal
language without changing scorer semantics.

Deliverables:

- Research-owned provider-neutral feature extraction adapter and recorded provider fixture
- `produce-signals-once` application service with deterministic Feature/Signal IDs
- preserved producer/model/prompt versions and `fundamental-scorer-v1`
- complete Observation -> Feature -> Signal lineage
- explicit feature-production failure state and retry eligibility
- forbidden action/order field contract tests

Observable behavior:

- Fed and BOJ Observations produce USD and JPY Features respectively.
- repeated production does not duplicate Feature or Signal records.
- malformed or forbidden provider output creates no neutral Signal.
- lineage and all version dimensions can be read from `fx_signal_store`.

Verification:

```powershell
python -m pytest -q tests/research_feature_production tests/persistence
python -m ruff check .
python -m mypy packages apps
```

### Milestone 4 — One-shot orchestration, HTTP reliability, and smoke path

Contribution: provide the smallest schedulable operational boundary before adding polling.

Deliverables:

- `python -m fx_research collect-once --source ... --database ...`
- `python -m fx_research produce-signals-once --database ...` with an explicitly configured
  extractor adapter
- timeout and bounded configurable retry for retryable HTTP GET failures
- source-aware errors and structured command results
- recorded end-to-end smoke tests and opt-in `source_smoke` tests for official endpoints

Observable behavior:

- one-shot commands are safe to rerun.
- network failure produces a failed fetch record and no Observation/Feature/Signal.
- a recorded full cycle persists immutable, versioned lineage.
- smoke tests are excluded from normal CI unless explicitly selected.

Verification:

```powershell
python -m pytest -q
python -m pytest -q -m source_smoke
python -m fx_research collect-once --source fed.press_monetary.rss --database tmp/news.sqlite3
python -m ruff check .
python -m mypy packages apps
```

## Migration and compatibility

- ExecPlan 0001 contracts and scorer semantics remain unchanged.
- Existing migration `0001_signal_lineage.sql` is never rewritten. Any shared schema change uses
  a new numbered migration.
- Research operational state is owned by `fx_research`, even when it shares a physical SQLite file
  with `fx_signal_store` for one-shot operation.
- Existing Observation/Feature/Signal rows are not backfilled or updated.
- Legacy Tavily, prompts, and `AiAction` are not imported or migrated.
- Source URL or HTML selector changes are configuration/adapter-version changes and must not reuse
  a normalizer version when normalized meaning changes.

## Validation and acceptance

ExecPlan 0002 is complete when:

- recorded Fed monetary-policy and speeches feeds normalize as separate USD sources;
- recorded BOJ monetary-policy and speeches listings normalize as separate JPY sources;
- repeated polling and production are idempotent across process runs;
- changed content at the same URL creates a new Observation;
- exact/missing/date-only publication time semantics are tested;
- malformed feed, changed HTML, missing body, and provider errors create no neutral Signal;
- producer/model/prompt/scorer versions and complete lineage are persisted;
- source smoke paths can reach official Fed and BOJ endpoints under an explicit marker;
- no Research/Live cross-import or Live invocation exists;
- Python 3.11 tests, Ruff, and mypy pass.

## Decision log

- 2026-07-13: Collection and Feature/Signal production begin in `apps/fx_research`; evaluation
  remains ExecPlan 0003.
- 2026-07-13: Initial sources are limited to official Fed RSS and BOJ HTML listings, mapped to USD
  and JPY by source configuration.
- 2026-07-13: BOJ is not treated as RSS. Date-only metadata remains evidence and does not create a
  publication timestamp.
- 2026-07-13: One-shot commands precede daemon or scheduler work.
- 2026-07-13: Cross-source merging and deferred providers remain outside the plan.
- 2026-07-13: Existing `fundamental-scorer-v1` semantics remain unchanged.
- 2026-07-13: `feedparser`, Beautiful Soup, and `pypdf` are confined to Research source/detail
  infrastructure; HTTP GET uses a small standard-library adapter with explicit bounded retry.
- 2026-07-13: Research polling state and shared lineage use one physical SQLite file in the
  one-shot CLI, while migrations and package ownership remain separate.
- 2026-07-13: The first Feature production CLI uses an explicitly supplied recorded structured
  provider. Selecting an external production LLM provider is not required to validate source
  ingestion and remains behind the existing `LlmFeatureExtractor` seam.
- 2026-07-13: Deterministic existing Feature IDs recover from a crash by reading the stored
  immutable Feature before scoring; the provider is not allowed to replace it.

## Progress

- [x] Read repository instructions, Foundation docs, ADRs, and applicable Skills.
- [x] Confirm official Fed and BOJ source families and record the investigation date.
- [x] Create ExecPlan 0002 before implementation.
- [x] Milestone 1 — Research application and recorded source contracts.
- [x] Milestone 2 — Deterministic normalization and operational idempotency.
- [x] Milestone 3 — Versioned Feature and Signal production.
- [x] Milestone 4 — One-shot orchestration, HTTP reliability, and smoke path.

Verification completed on 2026-07-13 with Python 3.11.9:

- normal suite: `74 passed, 2 skipped` (`source_smoke` skipped by default);
- opt-in official source smoke: `2 passed` for Fed monetary RSS and BOJ monetary HTML/PDF;
- Ruff: all checks passed;
- mypy: no issues in 43 source files;
- actual `collect-once` Fed run: first run `inserted=1`, second run `duplicates=1`.
