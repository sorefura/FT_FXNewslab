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

Observation/Featureを構築するBacktestでは、未来情報混入を避けるため`first_seen_at`を
重要視する。

source公開時刻だけで「当時利用可能だった」と判断しない。

persisted SignalのForward evaluation anchorは`signal.created_at`とする。`first_seen_at`は
sourceが利用可能になった時刻だが、Feature生成とscoringより前であり、その時点にはSignalが
まだ存在しない。target timeは`signal.created_at + forward horizon`で求める。

## Forward horizons

MVP:

- 15m
- 1h
- 4h
- 1d
- 3d

ExecPlan 0003では各Signal自身の`Signal.horizon`に関係なく、比較可能な観測scheduleとして
上記5 horizonをすべて作成する。

Signal targetとSignal typeごとに有効Horizonは異なるため、後続評価でその差を扱う。

すべてのSignalを同じ時間軸で優劣比較しない。

## Forward Result

最低限保存する。

```python
@dataclass(frozen=True)
class ForwardResult:
    signal_id: SignalId
    horizon: Horizon
    instrument: CurrencyPair
    projection_sign: int
    projection_version: str
    anchor_at: datetime
    target_at: datetime
    price_t0: Price
    price_tx: Price
    t0_observed_at: datetime
    tx_observed_at: datetime
    target_return_bps: Decimal
    mfe_bps: Decimal | None
    mae_bps: Decimal | None
    realized_volatility: float
    completed_at: datetime
    market_source: str
    market_data_version: str
    price_basis: str
    granularity: str
    formula_version: str
    snapshot_id: str
```

初期projectionは`USD -> USD_JPY (+1)`、`JPY -> USD_JPY (-1)`、
`USD_JPY -> USD_JPY (+1)`とし、versionは`currency-usdjpy-projection-v1`とする。
別々のUSD SignalとJPY Signalを合成してPair Signalを作らない。

Forward Result semanticsはprovider非依存とする。Market adapterは同じ`MarketCandle`へ
normalizeするが、`market_source`、`market_data_version`、`price_basis`、`granularity`を
結果とevidenceへ残す。GMO FX BIDとOANDA midpointは同一sampleとして暗黙に混ぜない。

Primary operational evidenceはGMO FX Publicの直接BID M1 OHLCとする。BID/ASK high/lowを
component単位で平均したsynthetic midpoint extremaは、同時刻に存在した保証がないため
使用しない。

### Forward return

market target returnは`projection_sign * ((price_tx / price_t0) - 1) * 10000`
で保存する。Signal directionでreturnを反転しない。directionはMFE/MAEのfavorable/adverse
方向にのみ使用する。

### MFE

Maximum Favorable Excursion。

Signal方向へ最大どこまで動いたか。

pathは`t0 <= candle open < tx`のcomplete M1 candleとし、tx candleのhigh/lowは含めない。
neutral directionではMFE/MAEをnullにする。

### MAE

Maximum Adverse Excursion。

Signalと逆方向へ最大どこまで動いたか。

### Realized volatility

Signal成績が単なる高Volatility regime依存でないかを確認する。

`price_t0`とpath candle closeのlog returnについて`sqrt(sum(r_i**2))`を計算する。
annualizeせず、dimensionless floatとして保存する。集約metricではない。

## Evaluation metrics

初期Evaluatorは`signal-validation-v1`、score definitionは
`signal-direction-v1`とし、`score = Signal.direction.value`を使用する。strengthと
confidenceは別score versionなしに掛けない。

Evaluation Datasetは一回のconsistent readで、completed `(Signal ID, ForwardResult ID)`、
非完了jobのID/status/strict cohort、unsupported target、incomplete horizonを含むexact
snapshotを固定する。PENDING、FAILED、UNAVAILABLE jobはzero returnへ変換せず、sample
exclusion diagnosticとして保持する。job statusを含むsnapshot全体とEvaluation設定が同じ
場合だけ既存runを再利用する。

headline metricのstrict cohortはSignal type/target、Signal horizon、Forward horizon、
producer/model/prompt/scorer/transformation version、market source/data version/basis/
granularity、projection/formula/score definition versionをすべて含む。GMO FX BIDとOANDA
midpoint、または異なるversionを暗黙に結合しない。

Evaluation Report保存時には、captured sampleがそのreportのstrict cohortに属し、全sampleが
重複なく一度だけ含まれることを永続化境界で再検証する。Validation Assessment保存時には、
Reportが同じEvaluation Runに属すること、policy content hash、および再計算したcohort/metric
payloadがpersisted Reportと一致することを検証する。同じResearch-owned pure derivationで
Assessment ID、status、condition resultsも再構築し、呼出側が改変したdecisionを保存しない。

### Information Coefficient

ScoreとForward Returnの順位相関を基本候補とする。

Pearsonを追加してもよいが、非線形性とoutlierを考慮し、Spearmanを優先候補とする。

初期実装はaverage rank tie処理、sample count、deterministic paired bootstrap 95% intervalを
保存する。3件未満、constant score、constant returnは0ではなくundefined reasonとする。

### Hit Rate

direction signとreturn signの一致率。

単独で主指標にしない。

neutral directionとzero returnはdenominatorから除外し、それぞれの件数、eligible count、
hit count、Wilson 95% intervalを保存する。

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

初期bucketは`[-1,-0.6)`, `[-0.6,-0.2)`, `[-0.2,0.2)`, `[0.2,0.6)`,
`[0.6,1]`で固定する。Pair scoreが範囲外でもclampせず、unbucketed countとして残す。
各bucketのcount/mean/median/interval/empty stateと、隣接step ratioおよびbucket meanの
Spearmanを保存する。

MFEとMAEは別々にeligible/null count、mean、median、quantileを集約する。neutral Signalの
null excursionを0へ変換しない。

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

初期sliceは`Signal.created_at`のcalendar quarterとする。3件未満のsliceは
`INSUFFICIENT_SAMPLE`でありfailureではない。Volatility regimeを導入する場合はversion付き
固定境界またはimmutable policy snapshotを要求し、dataset quantileから暗黙に境界を作らない。

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

ExecPlan 0004のAssessmentが生成できるstatusは`EXPERIMENTAL`、`PROMISING`、
`VALIDATED_FOR_RESEARCH`のみとする。通常Evaluationはreportだけを生成し、Assessmentには
version付きimmutable policyを明示入力する。policyはsample、Spearman/Hit Rateのpoint/CI、
bucket、monotonicity、stability条件を保持する。

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
