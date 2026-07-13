# Data and Versioning

## Principle

市場研究では「現在の最良値」より「当時何を知っていたか」が重要である。

そのため、Signal系データは履歴再現を優先する。

## Append-oriented records

原則append-orientedにする。

対象:

- observations
- features
- signals
- forward results
- evaluations
- live decisions
- risk decisions
- execution results

修正が必要な場合は新record/versionを作る。

## Identity

IDはdomain typeで区別する。

- ObservationId
- FeatureId
- SignalId
- CandidateId
- ExecutionIntentId
- BrokerOrderId

すべてを裸の`str`としてApplication全体へ流さない。

## Version dimensions

Signal再現に関係するversionを分ける。

### producer_version

Feature生成ロジック。

### model_version

LLM/ML model identifier。

### prompt_version

構造化抽出prompt。

### scorer_version

FeatureからSignalへのscore calculation。

### transformation_version

Currency SignalからPair Signal等への変換。

### strategy_version

Signal組合せとCandidate生成。

### risk_policy_version

risk limits/policy set。

バージョンを単一`app_version`だけで代用しない。

## Configuration snapshots

Live decisionでは、重要な設定を後から特定できる必要がある。

設定全文コピーまたはcontent hash + immutable config registryを用いる。

対象例:

- strategy weights
- entry threshold
- exposure limits
- freshness limit
- swap source priority

## Timestamps

UTC保存を基本とする。

表示時にtimezone変換する。

naive datetimeをdomainへ持ち込まない。

市場営業日処理、日足cutoff、swap付与日等は明示的なcalendar/time policyへ寄せる。

## Freshness

外部データはvalueだけでなくobserved timestampを持つ。

```text
value
source
observed_at
effective_at when known
```

RiskまたはApplication policyでstale dataを拒否できるようにする。

## Missing data

以下を区別する。

- zero
- neutral
- unknown
- unavailable
- stale
- not applicable

float `0.0`に集約しない。

## Suggested logical tables

### observation

```text
id
observation_type
source
source_timestamp
first_seen_at
content_hash
payload_reference
normalizer_version
```

### feature

```text
id
feature_type
target
value_payload
confidence
producer_version
model_version
prompt_version
created_at
```

### feature_source

```text
feature_id
observation_id
```

### signal

```text
id
target_type
target_value
signal_type
direction
strength
confidence
horizon
observed_at
created_at
scorer_version
```

### signal_source

```text
signal_id
feature_id
```

### forward_result

```text
signal_id
horizon
price_t0
price_tx
return_bps
mfe_bps
mae_bps
realized_volatility
market_data_version
completed_at
```

### trade_candidate

```text
id
strategy_id
strategy_version
pair
side
score
created_at
config_snapshot_id
```

### candidate_signal

```text
candidate_id
signal_id
```

### portfolio_decision

```text
candidate_id
decision
proposed_quantity
reason_code
exposure_snapshot_id
created_at
```

### risk_decision

```text
candidate_id
decision
reason_code
risk_policy_version
risk_snapshot_id
created_at
```

### execution_intent

```text
id
candidate_id
pair
side
quantity
order_semantics
idempotency_key
created_at
```

### order_result

```text
execution_intent_id
broker
broker_order_id
status
requested_price
filled_price
filled_quantity
submitted_at
completed_at
error_code
```

## Data migration

schema migrationとsemantic migrationを区別する。

Column追加だけで既存scoreの意味が変わる場合、それはsemantic migrationである。

旧Signalを新定義へ無理に書き換えない。

旧versionを保持し、新versionとの比較をResearchで行う。
