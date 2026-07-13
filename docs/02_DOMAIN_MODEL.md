# Domain Model

## Observation

Observationは外部世界で観測した事実のsnapshotである。

共通要件:

- unique id
- source
- observed_at
- source timestamp when available
- raw reference or immutable payload reference
- ingestion version when normalization is applied

例:

```python
@dataclass(frozen=True)
class NewsObservation:
    observation_id: ObservationId
    source: NewsSourceId
    title: str
    body: str
    published_at: datetime | None
    first_seen_at: datetime
    content_hash: str
```

Observationは「hawkish」「USD positive」を持たない。
それらはFeatureまたはSignalである。

## Feature

FeatureはObservationまたはMarket snapshotから抽出した測定表現である。

Featureはproducer情報を持つ。

```python
@dataclass(frozen=True)
class CurrencyFundamentalFeature:
    feature_id: FeatureId
    observation_ids: tuple[ObservationId, ...]
    currency: Currency
    monetary_policy: Score
    inflation: Score
    growth: Score
    employment: Score
    geopolitical_risk: Score
    confidence: Probability
    producer_version: str
    produced_at: datetime
```

Feature値の範囲は型またはconstructorで保証する。

例:

- `Score`: -1.0 <= x <= 1.0
- `Probability`: 0.0 <= x <= 1.0

floatを無制限に各Layerへ流さない。

## Signal

Signalは市場仮説である。

最低限以下を持つ。

```python
@dataclass(frozen=True)
class Signal:
    signal_id: SignalId
    target: SignalTarget
    signal_type: SignalType
    direction: DirectionScore
    strength: Probability
    confidence: Probability
    horizon: Horizon
    observed_at: datetime
    created_at: datetime
    source_feature_ids: tuple[FeatureId, ...]
    scorer_version: str
```

必要に応じて以下を追加できる。

- model_version
- prompt_version
- calibration_version

ただしprovider固有フィールドを直接domain型へ追加しない。
version metadataとして正規化する。

## Signal semantics

### direction

方向。

- `-1.0`: 強いnegative direction
- `0.0`: neutral
- `+1.0`: 強いpositive direction

Currency targetの場合は通貨価値方向。

Pair targetの場合はbase currency relative to quote currencyの方向。

### strength

仮説の大きさ。

directionの絶対値と同義にしない。

### confidence

producerが、そのFeature/Signal抽出自体をどの程度信頼しているか。

過去的中率ではない。

Researchで計算するhistorical reliabilityと混同しない。

### horizon

Signalが意味を持つと仮定する時間軸。

例:

- 15m
- 1h
- 4h
- 1d
- 3d

内部ではdurationまたは明示Enumで一貫させる。

## Currency first

Fundamental News Signalは原則Currency targetとして生成する。

```text
USD +0.60
JPY -0.20
```

Pair Signalが必要な場合、Currency Signalから導出できる。

```text
USDJPY = USD - JPY
       = +0.80
```

Pair transformationのversionを保存する。

Pairへ直接作用するイベントは例外としてPair targetを許容する。

例:

- pair-specific market dislocation
- instrument-specific liquidity event

例外を一般化しない。

## Currency Pair

Pairは文字列を各所でsplitしない。

```python
@dataclass(frozen=True)
class CurrencyPair:
    base: Currency
    quote: Currency
```

表示名はderived propertyとする。

```text
PairScore = BaseScore - QuoteScore
```

Currencyの`DirectionScore`は`[-1.0, 1.0]`、差分から導出するraw `PairScore`は
`[-2.0, 2.0]`の別bounded typeとして扱う。

PairScoreをCurrencyのDirectionScoreへ暗黙にclampまたはnormalizeしない。
将来normalizationが必要な場合は、別のtransformation versionとして導入する。

符号規約を全システムで統一する。

## Exposure

PositionをCurrency Exposureへ分解する。

概念例:

```text
USDJPY long:
USD positive exposure
JPY negative exposure
```

異なるPairのPositionをCurrency単位でaggregateする。

```text
USDJPY long
EURJPY long
GBPJPY long
```

は3つの独立Positionではあるが、Portfolio上はJPY short concentrationを形成する。

## Trade Candidate

Strategy出力。

```python
@dataclass(frozen=True)
class TradeCandidate:
    candidate_id: CandidateId
    strategy_id: StrategyId
    pair: CurrencyPair
    side: Side
    score: StrategyScore
    signal_ids: tuple[SignalId, ...]
    created_at: datetime
```

quantityをStrategyが確定する必要はない。

SizingはPortfolio/Risk責務とする。

## Portfolio Decision

Portfolio文脈を加えた候補評価。

候補のaccept/reduce/rejectとproposed sizeを持つ。

## Execution Intent

Risk承認後のみ生成する。

Broker APIへ必要な意味をdomain/application側の標準形式で表現する。

Broker固有order parameterはadapterで変換する。

## Result objects

以下を同一のstatus enumでまとめない。

- Signal evaluation result
- Portfolio decision
- Risk decision
- Order result

それぞれ異なる意味とlifecycleを持つ。
