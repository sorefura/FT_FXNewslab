# Swap Bot

## Validated Signal adoption

Before Strategy, `LiveAdoptionGate` compares a Signal with one Live-owned approval.
The approval fixes Strategy identity, exact Signal/cohort versions, target, Signal and
Forward horizons, market semantics, mode, effective time, and expiration. `None` is an
exact value, not a wildcard. Live authority starts at
`max(effective_from, approval.decided_at)`. A Signal created before that boundary is
not activated retroactively.

Approval and Revocation IDs represent semantic authority, while `decided_at`, actor,
and reason preserve the first successful write as audit metadata. A retry reuses that
record without moving the authority-start boundary or rewriting its audit trail.

The gate emits a Live-only `AuthorizedSignal` envelope and immutable
`SignalAuthorization`; approval metadata is not added to `fx_core.Signal`. Strategy
accepts authorized envelopes. The strict Candidate persistence path requires one
current authorization per contributing Signal and stores
`candidate_signal_authorization` lineage. It rechecks revocation and validity at
Candidate creation, including Signal creation and authorization against the same
authority-start boundary, so an old authorization cannot authorize a new Candidate.

`LIVE_ELIGIBLE` still means only Strategy-input eligibility. It does not mean
Portfolio acceptance, Risk approval, approved Execution intent, arming, or Broker
submission. ExecPlan 0005 acceptance remains shadow-only and records
`NOT_SUBMITTED`.

## Purpose

Swap Botは、Carryを中核要素とするLive Strategy Applicationである。

News BotでもAI Botでもない。

複数SignalとCarry条件を組み合わせ、PortfolioとRiskの制約下でTrade CandidateをExecutionへ渡す。

## Input families

初期候補:

- Carry Signal
- Currency Fundamental Signal
- Trend Signal
- Volatility Signal
- Market Risk Signal

Signal producerが異なってもStrategyは共通Signal contractを受ける。

## Carry and Spot separation

Carry RegimeとSpot Impactを分ける。

例:

```text
central bank hawkish event
├── spot impact
└── carry regime impact
```

両者は時間軸も評価方法も異なる。

単一のnews scoreへ統合してからStrategyへ渡さない。

## Strategy

Strategyは候補を生成する。

```text
Signals
  ↓
Strategy policy
  ↓
TradeCandidate
```

出力は`ENTRY_CANDIDATE`相当の意味であり、Orderではない。

Strategyが考慮できるもの:

- Signal alignment
- Carry attractiveness
- Signal horizon compatibility
- Strategy cooldown
- market regime eligibility

Strategyが所有しないもの:

- account margin hard limit
- total currency concentration
- broker order placement
- final quantity safety cap

## Signal combination

初期は明示的なweighted ruleを推奨する。

例:

```text
carry        0.40
fundamental  0.30
trend        0.20
volatility   0.10
```

weightは設定/versionとして保存する。

LLMに複数Signalを渡し、最終Actionだけを返させない。

初期段階では、判断系を追跡可能に保つ。

## Horizon compatibility

異なるHorizonを無条件に加算しない。

例:

```text
fundamental: 3d
trend:       4h
volatility: 15m
```

Strategyは自身のintended holding periodとの適合性を定義する。

短期Signalが長期Carry Positionを完全否定するのか、一時的entry delayに使うのかをStrategy policyで明示する。

## Portfolio

PortfolioはPair一覧ではなくExposureを理解する。

責務:

- existing positions aggregation
- pending intents aggregation
- currency exposure calculation
- pair concentration
- correlated concentration
- proposed sizing
- candidate prioritization

例:

```text
USDJPY LONG
EURJPY LONG
GBPJPY LONG
```

Pairは異なるがJPY short集中として扱う。

Portfolioは候補を以下へ分類できる。

- ACCEPT
- REDUCE
- REJECT

理由をstructured reason codeで保存する。

## Risk

Riskはhard constraintsを担当する。

初期候補:

- margin health
- max account drawdown
- max position size
- max currency exposure
- max pair exposure
- volatility guard
- stale data guard
- broker/account state guard
- duplicate execution guard

RiskはStrategy scoreを「なんとなく補正」するLayerではない。

Risk ruleが満たされない場合は明確に拒否する。

## Execution

Executionの責務:

- ExecutionIntent validation
- idempotency
- broker parameter mapping
- order submission
- broker response normalization
- retry policy where safe
- result persistence

Executionは以下を判断しない。

- USDが強いか。
- Newsがhawkishか。
- Carryが魅力的か。
- Strategy score threshold。
- Currency exposure limit値。

## Swap data

固定`manual_swap_overrides`はtemporary fallbackとして扱う。

目標境界:

```text
SwapDataSource
├── BrokerProvidedSwapSource
├── ExternalDynamicSwapSource
└── ManualOverrideSwapSource
```

優先順位とfreshnessをApplication policyで決める。

Manual override利用時はsourceとeffective periodを保存する。

不明なswap値を0として処理しない。

`unknown`と`zero`を分ける。

## Decision audit

Live decisionは以下を追跡できるようにする。

```text
Signal IDs
    ↓
Strategy version
    ↓
Candidate
    ↓
Portfolio decision + reason
    ↓
Risk decision + reason
    ↓
Execution intent
    ↓
Broker order/result
```

PnLだけを見てdecision chainを推測しない。

Risk評価とExecution Intent生成では、`RiskDecision.portfolio_decision_id`、
`PortfolioDecision.candidate_id`、`TradeCandidate.candidate_id`が同じchainを指すことを
検証する。別cycleの正当なdecisionを組み合わせても承認済みchainとして扱わない。
