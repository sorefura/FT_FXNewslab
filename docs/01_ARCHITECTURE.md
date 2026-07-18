# Architecture

## Production Strategy contract foundation and Paper target

Milestone 2-A now implements the immutable production Strategy config,
`OperationalSwapEvidence`, typed entry/exit evaluation and Candidate contracts,
production Strategy Ports, and the separate execution-authority mapping/guard. It
does not implement a concrete Strategy, Signal selection/materialization,
Portfolio/Risk integration, persistence, or Paper runtime.

Milestone 2-B1 now implements the package-neutral identity, immutable selection,
and transformation-authenticity foundation that precedes operational Pair Signal
persistence:

```text
PairSignalMaterializationSpecification
    -> stable Pair/as-of Request
    -> exact BASE/QUOTE candidate inventory
    -> terminal result derived from the complete candidate inventory
    -> immutable SELECTED / NO_MATCH / AMBIGUOUS snapshot
    -> deterministic Pair Signal ID
    -> exact shared CurrencyPairSignalTransformer output
    -> PairSignalDerivation
```

The Request excludes discovered Signal IDs, checkpoint, capture/materialization time,
worker, and retry metadata. `SignalContentSnapshot` commits to the full immutable
Signal plus canonical Feature/Observation lineage, and exact Observation ID set
equality defines a v1 source group. BASE and QUOTE remain ordered roles. Selection
snapshot semantic identity includes the complete candidate-set hash but excludes
first-write `captured_at`. Construction and hydration rerun the same pure resolver;
the caller cannot choose the terminal outcome or selected IDs. Ambiguity in any
complete group fails the Request closed, and semantic-rank ties do not use IDs or
Store sequence as winner tie-breakers. Exact source Signal lineage belongs to
`PairSignalDerivation`, not to the shared `Signal` model.

These contracts live in `fx_signal_store` and depend only on `fx_core`. A pure helper
reconstructs the exact selected Currency Signals, calls the unchanged shared
`CurrencyPairSignalTransformer`, and compares every Pair Signal semantic field with
exact equality. `PairSignalDerivation.validate_against()` separates content-addressed
intrinsic integrity from source/transformation relational authenticity. They do not
query SQLite.

Milestone 2-B2-A now adds the first persistence boundary:

```text
Signal append
    -> Signal + Feature lineage + one monotonic Store sequence in one transaction

Pair/as-of/Specification Request
    -> BEGIN IMMEDIATE
    -> exact Specification and Request append-or-compare
    -> first-write Claim(checkpoint_sequence, captured_at)
```

Legacy Signals receive one deterministic catalog sequence ordered by
`signals.created_at, signals.id`; this does not reconstruct historical insertion
order. `Signal.created_at` remains producer availability, while Store sequence is
the committed local-availability boundary. Retry returns the first Claim unchanged,
and later or old-dated backfills receive a larger Store sequence without changing
that Request checkpoint. Connection-scoped helpers keep the Claim transaction on one
SQLite connection. Claim is a retry anchor, not `SELECTED`, `NO_MATCH`, or
`AMBIGUOUS` terminal evidence.

Each numbered Signal Store migration executes its complete SQL body and
`schema_migrations` marker inside one explicit `BEGIN IMMEDIATE` writer transaction.
The marker is rechecked only after the writer boundary is acquired, so concurrent
initializers converge without rerunning the migration body. Statement, backfill, or
marker failure rolls the whole migration back instead of leaving partial unmarked
schema.

Checkpoint and Claim creation fail closed unless the current catalog proves exactly
one valid, hydratable Store entry for every Signal and no orphan entry. Retry also
requires a persisted positive checkpoint to reference an exact still-valid Store
sequence no greater than the current committed maximum. Catalog or Claim-boundary
corruption raises `SignalStoreIntegrityError` before any Specification, Request, or
Claim write commits.

Milestone 2-B2-B/C will persist the complete selection inventory/snapshot and exact
Pair Signal/derivation/completion artifacts. Milestone 2-B3 will query candidates and
compose the pure resolver/verifier into the operational materializer. Relational
validation remains mandatory before Pair artifact insert/reuse and after hydration.
No Live application dependency is admitted into the shared package direction.

Position exit evaluation is content-addressed from exact typed evidence rather than
only a business Position ID. `PositionExitPositionEvidence` self-describes the exact
business `PositionId`, immutable evidence ID, Pair, existing Side, and opened/observed
time. `PositionExitEvidenceContext` embeds that reference with Signal/Swap selection
checkpoints, expected Signal specification, prior Adoption decision, Adoption-state
evidence, and exit-input policy. Input, KEEP, close Candidate, and close evaluation
must match the typed Position/Pair/Side binding before an identity is generated.
Current authorized Pair Signal and operational Swap evidence contribute their
complete intrinsic lineage. Callers cannot inject arbitrary evidence IDs.

The remaining ExecPlan 0006 target is:

```text
Operational Signal Source
    -> Live Adoption Gate
    -> AuthorizedSignal
    -> ProductionEntryStrategy / NewsFilteredCarryStrategy (pending)
    -> ProductionTradeCandidate
    -> Portfolio
    -> Risk
    -> ApprovedExecutionIntent
    -> SHADOW_NOT_SUBMITTED or PaperExecutionGateway
    -> Paper Order/Fill/Position/Account/PnL/Swap evidence
```

`SHADOW_NOT_SUBMITTED`, `PAPER`, and `LIVE` are explicit, separate execution
authorities. ExecPlan 0006 can compose only the first two. The Paper adapter may share
approved-intent vocabulary, but it may not import, construct, or call the real Broker
Private transport. `LIVE` requires the separate ExecPlan 0007 authority.

Signal-input authorization remains a separate axis from execution authority:

```text
SHADOW_NOT_SUBMITTED -> Adoption RuntimeMode.SHADOW
PAPER                -> Adoption RuntimeMode.SHADOW
LIVE                 -> Adoption RuntimeMode.LIVE  (ExecPlan 0007 only)
```

Paper operation therefore uses existing SHADOW authorization while retaining PAPER
in operational lineage. `RuntimeMode.LIVE` is neither required nor permitted for
Paper, and it does not itself grant Live Broker execution approval.

Operational recovery separates stable semantic work from audit attempts:

```text
CycleSlot(schedule/as-of/authority/Strategy/cycle-policy)
    -> first-claim immutable CycleInputSnapshot
    -> one or more append-only CycleAttempts
    -> approved intent
    -> immutable FillEvaluationPlan
    -> one or more ordered FillEvaluationSteps
        -> zero or more append-only PENDING attempts
        -> one terminal StepResolution
            -> MarketObservationSelection -> zero or one PaperFill
            -> NoMarket / Cancelled / Expired outcome
    -> deterministic Paper ledger outputs
```

Variable input IDs do not create a new cycle identity. The first claim atomically
freezes Signal/authorization/swap/market/Position/Account/checkpoint and selection/
freshness-policy lineage. Retry reads that snapshot; late or backfilled data applies
only to a later slot. Paper order creation likewise freezes one plan's original
quantity, Step schedule/terminal boundary, policy versions, and seed root. Each Step
freezes its own window/due boundary, remaining-before quantity, versions, and seed.
Its market evidence is selected once by received time, provider time, then observation
ID, and reused after restart.

PENDING is an append-only evaluation attempt, not Step resolution. The same Step may
accumulate PENDING attempts and later select a pre-due quote without rewriting them.
A positive partial fill may create only the next contiguous Step with remaining
quantity derived from persisted ordered Fills. Step terminal resolution and order
terminal state are separate: `PARTIALLY_FILLED` may continue, while `FILLED`,
`CANCELLED`, `EXPIRED`, and `REJECTED` cannot create another Step.

Paper market data is Live-owned public observation evidence. It separates provider
timestamp, local receipt/availability, and evaluation time and must be available
after the approved intent and inside the active Step's frozen market window/due
boundary. Research `ForwardResult` is forbidden as fill input.

Current executable behavior remains the ExecPlan 0005 authorized shadow path: it
reaches an approved intent and records `NOT_SUBMITTED`. M2-B2-A changes only the
shared Signal Store and Request Claim persistence boundary. The M2-A production
contracts are not connected to Portfolio, Risk, Execution, or persistence, and there
is no selection-snapshot store, Pair materializer, concrete production Strategy,
Paper Gateway, Paper ledger, or operational daemon.

## Research-to-Live adoption boundary

Research and Live remain sibling applications. The only adoption flow is:

```text
Research Validation Evidence
    -> explicit assessment-ID read at approval time
    -> immutable Live-owned evidence snapshot
    -> explicit Live Strategy adoption decision
    -> Live-only runtime adoption gate
    -> AuthorizedSignal
    -> Strategy -> Portfolio -> Risk -> Execution
```

`swap_bot` owns the evidence-source Port and read-only SQLite adapter but imports no
`fx_research` module. Normal Strategy cycles do not read the Research database. The
gate never imports or calls Broker/Execution and never mutates `fx_core.Signal`.
Zero or multiple exact approvals, version/target/horizon mismatch, invalid time,
revocation, and runtime-mode mismatch all fail before Strategy.

## Architectural style

Modular Monolithを基本とする。

アプリケーション境界は分けるが、初期段階では分散システム化しない。

```text
packages/fx_core
apps/fx_research
apps/swap_bot
```

依存方向は内側へ向ける。

```text
Infrastructure
      ↓
Application
      ↓
Domain
```

DomainはBroker SDK、HTTP client、LLM SDK、ORM frameworkへ依存しない。

## Shared flow

```text
Source Adapter
    ↓
Observation
    ↓
Feature Producer
    ↓
Feature
    ↓
Signal Producer
    ↓
Signal
```

ここまでをResearchとLiveで意味的に共有する。

「同じPython関数を必ず呼ぶ」という意味ではない。

同一のdomain contract、versioning rule、semantic definitionを共有する。

Operational News collectionはResearch applicationが所有する。Source adapterがFed RSSや
BOJ HTML/PDFを`CollectedNewsItem`へ閉じ込め、normalization後の`NewsObservation`だけを
共有contractへ渡す。Source configurationがcandidate currencyを決定し、LLMへcurrency
selectionを委譲しない。

Operational Feature productionもResearch applicationが所有する。OpenAI等の外部provider
固有request/responseはInfrastructure adapter内でprovider-neutralなstructured payloadへ
変換し、`ProviderLlmFeatureExtractor`だけがdomain value検証とVersionMetadata付与を行う。
外部provider障害やmalformed responseをneutral Featureへ変換しない。

## Research path

```text
Signal
  ↓
Signal Store
  ↓
Forward Observer
  ↓
Forward Result
  ↓
Evaluator
  ↓
Metrics
  ↓
Validation Decision
```

Researchの出力は統計、評価結果、Signal specificationである。

ResearchからBroker orderを作らない。

Forward ObserverはResearch applicationが所有する。Primary adapterはGMO FX Publicの
`USD_JPY` M1 BID KLineを取得し、provider response timeからcompleteと保証できるcandleだけを
Research contractへ変換する。OANDA v20 midpointは異なるmarket semanticsを持つoptional
adapterとする。Job state、immutable MarketSnapshot、append-only ForwardResultはResearch
SQLite schemaに保存する。共有`fx_core`とLive applicationへMarketCandleやForwardResultを
持ち込まない。

## Live path

```text
Signal
  ↓
Strategy
  ↓
Trade Candidate
  ↓
Portfolio
  ↓
Portfolio Decision
  ↓
Risk
  ↓
Execution Intent
  ↓
Execution
  ↓
Broker
```

各段階で出力型を分ける。

`Signal`、`TradeCandidate`、`PortfolioDecision`、`ExecutionIntent`、`OrderResult`を同一型にしない。

## Layer responsibilities

### Observation

外部で何を観測したかを表す。

判断を含めない。

### Feature

Observationから抽出した意味のある測定値を表す。

Feature ProducerはLLM、Rule、Statistical logicを使用できる。

### Signal

市場に関する方向、強度、信頼度、対象、Horizonを持つ仮説。

注文ではない。

### Strategy

複数Signalと市場条件から、戦略上の候補を作る。

### Portfolio

候補を既存PositionとExposureの文脈で評価する。

### Risk

許容損失、Margin、Volatility、Concentration等のhard constraintを評価する。

### Execution

承認済みExecution IntentをBroker orderへ変換する。

売買戦略を持たない。

## Ports

外部境界はProtocol/Portで表現する。

初期候補:

- NewsSource
- MarketDataSource
- SwapDataSource
- BrokerGateway
- LlmFeatureExtractor
- Clock
- IdGenerator
- ObservationRepository
- SignalRepository
- ForwardResultRepository
- PositionRepository

Portは「差し替えられそうだから」作るのではない。

外部I/O、時間、乱数/ID、永続化、provider固有処理をdomain/applicationから隔離するために作る。

## Dependency prohibitions

```text
Domain -> Infrastructure          prohibited
Strategy -> Broker SDK            prohibited
Feature -> Execution              prohibited
Research -> Execution             prohibited
Execution -> News                 prohibited
Risk -> LLM                       prohibited
fx_core -> swap_bot               prohibited
fx_core -> fx_research            prohibited
```

## Error policy

Domain errorとexternal errorを分ける。

例:

- `InvalidSignalError`
- `UnsupportedCurrencyPairError`
- `RiskLimitExceeded`

外部:

- `BrokerUnavailable`
- `NewsSourceUnavailable`
- `LlmProviderFailure`

外部障害をdomain上の「弱いSignal」へ変換しない。

データ未取得と弱い市場見通しは異なる事象である。
