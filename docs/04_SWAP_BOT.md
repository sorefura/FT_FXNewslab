# Swap Bot

## Current operational gap

The current implementation has an authorization-aware accepted 0005 Strategy
Protocol, immutable Candidate/Portfolio/Risk/approved-intent contracts, swap
availability semantics, and an authorized shadow cycle that records `NOT_SUBMITTED`.
Milestone 2-A additionally provides production Strategy config/identity, typed entry
and exit Ports/results, lossless `ProductionTradeCandidate`, versioned
`OperationalSwapEvidence`, `PositionCloseCandidate`, and execution-authority mapping.
It has no concrete production Strategy, operational Signal/Swap adapter, production
Candidate persistence, ordinary close Portfolio/Risk path, Paper Gateway, fill
engine, account/PnL ledger, scheduler, or daemon.

Accepted 0005 `TradeCandidate` remains entry-only and unchanged.
`ApprovedLiquidationIntent` is created only for the
Risk margin kill switch and is not a normal Strategy close path. `AccountSnapshot`
contains only margin ratio, while `SwapQuote` does not yet carry the unit/settlement/
rollover/version identity needed for cash accrual. Test fixture values are not
production defaults.

## ExecPlan 0006 M2-A contracts and NewsFilteredCarryStrategy target

The production Ports consume exact `AuthorizedSignal` envelopes and versioned swap
evidence. The implemented immutable config fixes Strategy/config identity,
eligible Pairs, Pair transformation version, positive/negative thresholds, neutral
band, and Signal/swap freshness. Initial Pair scope is exactly `USD_JPY` and
`MXN_JPY`.

Signal remains currency-first. The operational Pair Signal must be generated and
persisted using `fx_core.CurrencyPairSignalTransformer`:

```text
PairScore(base/quote) = CurrencyScore(base) - CurrencyScore(quote)
```

The Strategy does not reimplement this formula or substitute zero for a missing
currency. A score strictly above the positive threshold is BUY-eligible; a score
strictly below the negative threshold is SELL-eligible; equality and the neutral band
produce no entry. BUY additionally requires strictly positive fresh long received
swap, while SELL requires strictly positive fresh short received swap. Missing,
unknown, unavailable, stale, malformed, zero, or negative swap produces a structured
skip.

The accepted 0005 Candidate score type is a `Probability`, while Pair direction is a
`PairScore` in `[-2, 2]`. M2-A resolves this without altering 0005:
`ProductionTradeCandidate` stores lossless `PairScore` and `Probability` confidence
in separate fields and contains no quantity or Broker parameter.

The accepted 0005 Protocol returns one Candidate or `None`. The separate production
contract evaluates one configured Pair at a time in deterministic Pair order and
returns a typed Candidate-or-structured-skip result, so `None` does not hide
operational causes and a second eligible Pair is not silently discarded.

Strategy entry and ordinary exit are separate typed Ports. The implemented
`PositionCloseCandidate` is always reduce-only, has a structured exit reason, and has
no quantity or action string. Portfolio quantity, partial-close validation, approved
close intent, and Risk no-overclose checks remain M2-D. Risk emergency liquidation
remains a different authority.

Exit input lineage distinguishes the business `PositionId` from the exact immutable
Position snapshot/event evidence. It also commits to position opened/observed time,
current Signal/Authorization/Adoption lineage when present, exact operational Swap
evidence when present, Signal and Swap selection checkpoints, expected Signal
specification, Adoption-state evidence, config identity, and exit-policy version.
Both KEEP and close results preserve that evidence. Close factories derive
`PositionCloseEvidenceLineage` from the typed evaluation input and enforce
reason-specific evidence; no caller-controlled generic evidence tuple is accepted.

The v1 config accepts only `production-trade-candidate-v1`, `currency-pair-v1`, and
`pair_fundamental`. Unsupported downstream contracts fail when config is built.
Operational Swap amounts remain Decimal text in content identity, but AVAILABLE and
numeric STALE evidence require finite values; NaN and infinities are rejected while
positive, negative, zero, and signed zero remain representable.

## ExecPlan 0006 target: Paper authority

```text
SHADOW_NOT_SUBMITTED != PAPER_EXECUTED != LIVE_EXECUTED
```

The operational mode is an explicit enum, never one dry-run boolean. Paper accepts
only approved entry/close/liquidation intents, uses deterministic versioned market and
swap evidence, and appends fictional order/fill/position/account/PnL records. It
cannot import or construct the real Broker Private transport. `LIVE` is rejected
until ExecPlan 0007.

Execution authority and Adoption authorization are separate. Both
`SHADOW_NOT_SUBMITTED` and `PAPER` authorize Signals with existing
`RuntimeMode.SHADOW`; `LIVE` maps to `RuntimeMode.LIVE` only under ExecPlan 0007.
Consequently `SHADOW_ONLY` and `LIVE_ELIGIBLE` approvals can both support Paper input,
but neither a Paper cycle nor Paper result can create `RuntimeMode.LIVE` authorization
or Live Broker authority. The Paper cycle separately records
`ExecutionAuthorityMode.PAPER`; no `RuntimeMode.PAPER` is introduced.

Order lifecycle includes accepted, rejected, open, partially filled, filled,
cancelled, and expired states. Restart/retry must reconcile append-only state without
duplicating an order, fill, ledger entry, or swap accrual. Paper burn-in is evidence,
not Live authority.

One semantic schedule slot is identified by scheduled/as-of time, execution
authority, Strategy/config identity, and cycle-policy version. Its first claim
atomically freezes one immutable input snapshot containing exact Signal,
authorization/adoption, swap/market, Position/Account, checkpoint, and selection/
freshness-policy lineage. Inputs are not part of the slot ID. Retrying adds a separate
attempt record and reuses the frozen snapshot; a newly arrived or corrected input is
for a later slot.

One approved intent similarly freezes exactly one `FillEvaluationPlan`, including
original Decimal quantity, Step schedule/terminal boundaries, exact model/policy
versions, and seed root. The plan owns contiguous ordered `FillEvaluationStep`
records. Each Step freezes its market window/due boundary,
`remaining_quantity_before`, relevant versions, and derived seed.

Before due, absence of eligible evidence appends a
`PENDING_NO_ELIGIBLE_MARKET` attempt; PENDING is not a terminal Step resolution and is
never updated. The same Step may later select an eligible quote. Each resolved Step
has exactly one cross-variant terminal resolution: selected market, no-market,
cancelled, or expired. Selection uses the exact Pair inside that Step's window and
orders eligible observations by received time, provider time, and observation ID.
The immutable selection is stored before its zero-or-one Fill and reused after
restart. A newer quote cannot revise that Step, although a later Step may select new
evidence inside its own window. Research Forward Result is never eligible.

A positive partial fill leaves
`remaining_quantity_after = remaining_quantity_before - fill_quantity` and may create
only the next contiguous Step when versioned policy permits. Remaining quantity is
rebuilt from original quantity and immutable ordered Fills, not mutable current
state. Zero fill creates no Fill, total fill cannot exceed original quantity, and an
order in `FILLED`, `CANCELLED`, `EXPIRED`, or `REJECTED` cannot create another Step.
`PARTIALLY_FILLED` is nonterminal for the order even though its producing Step is
terminally resolved.

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
