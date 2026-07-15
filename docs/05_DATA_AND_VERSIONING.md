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
- market candle revisions
- market snapshots
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

### projection_version

Signal targetを観測instrumentへ写像する規約。初期値は
`currency-usdjpy-projection-v1`。

### market_data_version

provider responseからimmutable MarketCandleへ変換するadapter/market contract。

Primary GMO FX semanticsは`gmo-fx-kline-bid-v1`、optional OANDA semanticsは
`oanda-v20-candles-v1`とする。別basisのrecordを同一versionへ入れない。

### formula_version

alignment、return、MFE/MAE、realized volatilityを含むForwardResult計算規約。
初期値は`forward-result-v1`。

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

News ingestionでは、collectorが同じnormalized contentを最初に認識した時刻を
`first_seen_at`として固定する。Sourceが日付だけを示す場合、00:00 UTC等を補わず
`published_at=None`とし、日付文字列はResearch-owned ingestion evidenceへ保存する。

Observation IDはsource identity、canonical payload URL、normalized content hashから
決定的に生成する。同じURLの同じcontentは再保存せず、同じURLでもcontentが変化した
場合は別のimmutable Observationとする。

Feature/Signal productionでは次の順序を保証する。

```text
observation.first_seen_at
  <= feature.created_at
  <= signal.created_at
  <= production.updated_at
```

Signal時刻はFeature取得後、production時刻はSignal保存後に取得する。既存Featureを再利用
する場合は注入Clockと既存record時刻の大きい方を使い、架空の時間加算は行わない。
Featureがsource Observationより過去、または既存Signalがsource Featureより過去の場合は
recordを書き換えずproduction failureとする。

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

Forward observationではprovider/transport/contract failureを`FAILED`、alignment window内に
complete candleがない状態を`UNAVAILABLE`として分ける。後者は
`T0_CANDLE_NOT_AVAILABLE`または`TARGET_CANDLE_NOT_AVAILABLE`を保持し、どちらもzero returnを
生成しない。

M1 alignmentのoperational readinessは`target_at + alignment delay + 1 minute`とする。
alignment終端candleがclose可能になる前はproviderを呼ばず`PENDING`を維持する。

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
result_id
signal_id
horizon
instrument
projection_sign
projection_version
anchor_at
target_at
price_t0
price_tx
t0_observed_at
tx_observed_at
target_return_bps
mfe_bps
mae_bps
realized_volatility
market_source
market_data_version
price_basis
granularity
formula_version
snapshot_id
completed_at
```

ForwardResultとmarket evidenceを分離する。

```text
market_candle_revision
  revision_id
  source / instrument / granularity / price_basis
  open_time
  decimal OHLC
  complete
  market_data_version

market_snapshot
  snapshot_id
  captured_at

market_snapshot_candle
  snapshot_id
  ordinal
  candle_revision_id
```

同じopen timeでもcontentが変われば別revisionとする。Snapshotはt0からtxまで実際に使用した
complete candle revisionを順序付きで参照する。Forward jobの
`PENDING/COMPLETED/FAILED/UNAVAILABLE`はretryのためのmutable operational stateであり、
immutable evidence/result tableとは分離する。

Forward evaluation sampleの比較・集約では最低限、次のsemantic dimensionsを保持する。

```text
market_source
market_data_version
price_basis
granularity
projection_version
formula_version
```

ExecPlan 0004は異なるdimensionを無条件に一つのheadline metricへ集約しない。

### evaluation_run

```text
run_id
evaluator_version
score_definition_version
cohort_definition_version
ordered_input_identity_hash
metric_configuration_json
bootstrap_configuration_json
created_at
```

`evaluation_run_input`はordinal付きでexact Signal IDとForwardResult IDを参照する。一回の
SQLite read transactionでinput setを固定し、計算中に追加されたForwardResultは既存runへ
含めない。run identityはordered completed inputだけでなく、非完了job ID/statusとそのcohort、
unsupported Signal ID、incomplete-horizon Signal ID、および全configurationを含む。同じfull
snapshotとconfigurationだけが同じrunを再利用する。full snapshotはcanonical JSONとcontent
hashでappend-only保存し、診断を後からcurrent job stateで再構成しない。

`evaluation_report`はrun内のstrict cohortごとにcohort identityとversion付きmetric payloadを
保存する。cohort identityにはSignal/Forward horizon、Signal version群、market semantics、
projection/formula/score definition versionを含める。

`validation_policy`と`validation_assessment`はReportから分離する。policy versionは同じ名前で
contentを変更できず、AssessmentはEvaluation Run、Report、policy version/content hashを参照する。
Assessment保存時には参照の存在だけでなく、Reportのrun所属、persisted policy hash、および
再計算したstrict cohort/metric payloadとpersisted Reportの一致を検証する。さらに同じpure
derivationからAssessment ID、status、condition resultsを再生成し、完全一致するdecisionだけを
保存する。
run/input/report/policy/assessmentはすべてappend-onlyとし、UPDATE/DELETEを拒否する。

Evaluation metricのbootstrap version、seed、iteration count、bucket boundaries、quantilesは
configuration snapshotへ保存する。統計上のundefined、insufficient、neutral、zero return、
null MFE/MAEを同じ値へ集約しない。

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
