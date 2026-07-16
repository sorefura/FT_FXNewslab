# Architecture

## Production and Paper operations target

The following is the ExecPlan 0006 target and is not implemented at the current
planning milestone:

```text
Operational Signal Source
    -> Live Adoption Gate
    -> AuthorizedSignal
    -> NewsFilteredCarryStrategy
    -> TradeCandidate
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
    -> immutable MarketObservationSelection or terminal no-market evidence
    -> deterministic Paper fill/ledger outputs
```

Variable input IDs do not create a new cycle identity. The first claim atomically
freezes Signal/authorization/swap/market/Position/Account/checkpoint and selection/
freshness-policy lineage. Retry reads that snapshot; late or backfilled data applies
only to a later slot. Paper order creation likewise freezes fill due time, policy
versions, and seed. Market evidence is selected once by received time, provider time,
then observation ID, and reused after restart.

Paper market data is Live-owned public observation evidence. It separates provider
timestamp, local receipt/availability, and evaluation time and must be available
after the approved intent and by the frozen due boundary. Research `ForwardResult` is
forbidden as fill input.

Current implementation remains the ExecPlan 0005 authorized shadow path: it reaches
an approved intent and records `NOT_SUBMITTED`. There is no production Strategy,
Paper Gateway, Paper ledger, or operational daemon yet.

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
