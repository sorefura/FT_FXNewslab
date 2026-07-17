# Architecture

## Production Strategy contract foundation and Paper target

Milestone 2-A now implements the immutable production Strategy config,
`OperationalSwapEvidence`, typed entry/exit evaluation and Candidate contracts,
production Strategy Ports, and the separate execution-authority mapping/guard. It
does not implement a concrete Strategy, Signal selection/materialization,
Portfolio/Risk integration, persistence, or Paper runtime.

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
reaches an approved intent and records `NOT_SUBMITTED`. The M2-A production contracts
are not connected to Portfolio, Risk, Execution, or persistence, and there is no
concrete production Strategy, Paper Gateway, Paper ledger, or operational daemon.

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
