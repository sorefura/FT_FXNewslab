# Signal and Research

## Objective

Researchの目的は、Signalが未来を「当てたか」を単純集計することではない。

以下を測定する。

> どのSignal typeが、どのtargetに対し、どのHorizonで、どの程度の情報量を持つか。

## Ex-ante principle

Signalは事前情報として保存する。

作成後、Forward Resultを見て以下を変更してはならない。

- direction
- strength
- confidence
- horizon
- source features
- scorer/model/prompt version

修正版は新しいSignalとして生成する。

## Timestamps

最低限区別する。

- `published_at`: sourceが示す公開時刻
- `first_seen_at`: collectorが初めて取得した時刻
- `feature_created_at`
- `signal_created_at`
- `price_observed_at`

Backtest/Forward evaluationでは、未来情報混入を避けるため`first_seen_at`を重要視する。

source公開時刻だけで「当時利用可能だった」と判断しない。

## Forward horizons

MVP:

- 15m
- 1h
- 4h
- 1d
- 3d

Signal targetとSignal typeごとに有効Horizonは異なる。

すべてのSignalを同じ時間軸で優劣比較しない。

## Forward Result

最低限保存する。

```python
@dataclass(frozen=True)
class ForwardResult:
    signal_id: SignalId
    horizon: Horizon
    price_t0: Price
    price_tx: Price
    return_bps: BasisPoints
    mfe_bps: BasisPoints
    mae_bps: BasisPoints
    realized_volatility: float
    completed_at: datetime
    market_data_version: str
```

### Forward return

Signal directionと将来returnの関係を見る。

### MFE

Maximum Favorable Excursion。

Signal方向へ最大どこまで動いたか。

### MAE

Maximum Adverse Excursion。

Signalと逆方向へ最大どこまで動いたか。

### Realized volatility

Signal成績が単なる高Volatility regime依存でないかを確認する。

## Evaluation metrics

### Information Coefficient

ScoreとForward Returnの順位相関を基本候補とする。

Pearsonを追加してもよいが、非線形性とoutlierを考慮し、Spearmanを優先候補とする。

### Hit Rate

direction signとreturn signの一致率。

単独で主指標にしない。

### Monotonicity

score bucketが高くなるほどforward returnが一方向へ変化するか。

例:

```text
[-1.0, -0.6) -> negative mean return
[-0.6, -0.2) -> smaller negative
[-0.2,  0.2) -> near zero
[ 0.2,  0.6) -> positive
[ 0.6,  1.0] -> larger positive
```

完全な単調増加のみを合格条件にする必要はない。

sample sizeとconfidence intervalを併記する。

### Stability

以下でsliceする。

- month/quarter
- volatility regime
- currency
- pair
- event type
- source
- signal producer version

一部期間だけの効果を全期間のSignal品質とみなさない。

## Evaluation unit

基本キー:

```text
signal_type
target
horizon
scorer_version
```

必要に応じて:

```text
event_type
currency
source
market_regime
```

を追加する。

sliceを細かくしすぎてsample sizeを失わない。

## Validation

Research上の「有用」はbinaryだけにしない。

例:

```text
EXPERIMENTAL
PROMISING
VALIDATED_FOR_RESEARCH
APPROVED_FOR_STRATEGY
DEPRECATED
```

`APPROVED_FOR_STRATEGY` は統計だけで自動昇格させない。

Strategy上の利用方法、failure mode、sample size、regime dependencyを確認する。

## Live feedback

Live PnLをSignal正解ラベルとして直接使わない。

PnLには以下が混在する。

- Signal quality
- Strategy combination
- Position sizing
- Risk rejection
- execution price
- spread/slippage
- holding period
- exit logic

SignalはForward Resultで評価する。

StrategyはStrategy outcomeで別評価する。

Executionはslippage/fill qualityで別評価する。
