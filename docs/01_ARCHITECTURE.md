# Architecture

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
