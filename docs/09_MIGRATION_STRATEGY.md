# Migration Strategy

## Principle

Big bang rewriteを行わない。

現在動作するSwap Botを止めて新実装完成を待つのではなく、新境界を導入して旧処理を段階的に置換する。

## Phase 0: Inventory

コード変更前に現行コードを責務マッピングする。

分類:

- Observation
- Feature
- Signal
- Strategy
- Portfolio
- Risk
- Execution
- Infrastructure
- Mixed/Unknown

特に検出する。

- LLM outputから直接side/actionを決める箇所
- News scoreをRisk判断に混ぜる箇所
- Broker response型がdomainへ漏れる箇所
- Positionをpairだけで集計する箇所
- manual_swap_overridesへの依存
- prompt/model/scorer version未保存
- 時刻がnaive datetimeの箇所
- mutable historical records

## Phase 1: Introduce domain vocabulary

最初に巨大な処理移動をしない。

以下の安定した型から導入する。

- Currency
- CurrencyPair
- Horizon
- ObservationId
- FeatureId
- SignalId
- Signal
- TradeCandidate

旧コードとのadapterを許容する。

このPhaseの目的は、新しい言語をコードへ導入すること。

## Phase 2: Isolate external adapters

Broker、News、Market Data、Swap、LLMをApplicationからPort経由にする。

最優先はBroker SDKの漏れを止める。

旧implementationをadapter内部で再利用してよい。

## Phase 3: Extract Signal path

現行News/AI判断から以下を分離する。

```text
Raw News
  ↓
News Observation
  ↓
Currency Feature
  ↓
Currency Signal
```

この段階でLive売買へ接続しなくてもよい。

Signalを保存し、Researchへ流せることを優先する。

## Phase 4: Build Research loop

追加する。

- Forward Observer
- Forward Result
- basic IC
- Hit Rate
- Score bucket
- MFE/MAE

既存BotのPnLをSignal評価に流用しない。

## Phase 5: Extract Strategy

旧売買判断を`TradeCandidate`生成へ変換する。

当初は旧ロジックをStrategy内で再現してもよい。

重要なのは、StrategyからBroker callを消すこと。

## Phase 6: Introduce Portfolio exposure

Position snapshotをCurrency Exposureへ分解する。

最初はshadow evaluationを推奨する。

```text
old live decision
new portfolio decision
```

を並行記録し、注文拒否にはまだ使用しない。

差分を観測後、policyを調整する。

## Phase 7: Enforce Risk boundary

Risk decisionを明示型へする。

Executionはapproved ExecutionIntentのみ受け付ける。

Risk bypass pathを削除する。

## Phase 8: Dynamic swap source

`manual_swap_overrides`を`ManualOverrideSwapSource`へ隔離する。

動的sourceを追加する。

source priority、freshness、unknown handlingを明示する。

Manual override fallbackの利用をaudit可能にする。

## Phase 9: Strategy consumes validated Signals

Researchで評価したSignal specificationをStrategyへ採用する。

Research implementationそのものをLive importしない。

共有するのはcontract/configuration/specificationである。

## Compatibility rule

移行期間はanti-corruption adapterを許容する。

ただしtemporary adapterには削除条件をissue/ExecPlanへ書く。

コードコメントで「TODO refactor later」とだけ残さない。

## Rollout

Live behaviorを変えるPhaseでは可能な限り以下を使う。

- shadow mode
- dry run
- decision diff logging
- feature flag
- limited pair scope

最初から全Pairで新Strategyを有効化しない。

## Migration completion

刷新完了条件:

- LLM/NewsからBrokerへの直接pathがない。
- StrategyからBroker SDK importがない。
- ExecutionがStrategy scoreを解釈しない。
- ResearchがLive Execution moduleをimportしない。
- Signal lineageをObservationまで追跡できる。
- Currency ExposureをPortfolio判断へ使用できる。
- manual swap fallback利用が識別可能。
- historical Signalがversion付きimmutable recordである。
