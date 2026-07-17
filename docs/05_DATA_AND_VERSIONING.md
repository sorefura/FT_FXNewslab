# Data and Versioning

## Production Strategy identity and Paper evidence target

Milestone 2-A adds content-addressed Strategy config, operational Swap evidence,
entry evaluation, production Candidate, and ordinary close Candidate contracts in
code without adding persistence. Existing Live migrations remain `0001` and `0002`.
Milestone 2-B/C/D may add Strategy persistence using the next available additive
numbers in implementation order. Paper persistence begins at the next available
additive Live migration only after Milestone 2 production Strategy persistence is
complete; `0003` is not reserved.

Position exit semantic identity embeds one `PositionExitPositionEvidence` payload
containing the business Position ID, distinct immutable Position evidence ID, Pair,
existing Side, and opened/observed timestamps. Evaluation input must exactly match
that self-described Position/Pair/Side before identity creation. The lineage also
includes exact current Signal/Authorization/Adoption and Swap identities or explicit
absence, selection checkpoints, expected Signal specification, Adoption-state
evidence, config/policy versions, evaluation time, and outcome/reason. KEEP is
decision evidence and retains this full lineage. Ordinary close Candidate identity
embeds the same typed lineage; it is not assembled from caller-supplied strings.

`OperationalSwapEvidence` content identity continues to serialize Decimal amounts
as exact text. Only finite Decimal amounts are valid for AVAILABLE or numeric STALE
evidence; no float conversion participates in validation or identity. The supported
v1 Candidate contract, Pair transformation, and Pair Signal type are explicit config
identity dimensions and unsupported values cannot enter a valid config.

The following Paper records remain target contracts:

```text
paper_cycle_slot / paper_cycle_input_snapshot / paper_cycle_attempt
paper_order / paper_order_event
paper_fill_evaluation_plan / paper_fill_evaluation_step
paper_fill_evaluation_attempt / paper_market_observation_selection
paper_step_terminal_outcome
paper_fill
paper_position_event or paper_position_snapshot
paper_ledger_entry / paper_account_snapshot
paper_swap_evidence / paper_swap_accrual
paper_reconciliation_result
paper_burn_in_report
```

These records are append-oriented. Semantic IDs commit to canonical content and
version dimensions; audit attempt/time remains separate. Built-in `hash()` is not a
persistent identity. Current projections must be rebuildable from exact ordered
events and checked by reconciliation.

`CycleSlotId` commits to UTC `scheduled_for`, UTC `as_of`, execution-authority mode,
Strategy ID/version/config identity, and cycle-policy version. It does not include
selected input IDs. The schema must enforce one row for that semantic slot and one
`paper_cycle_input_snapshot` per slot. First claim selects and inserts the snapshot in
one transaction; a conflicting second snapshot rolls back in full.

The input snapshot records its slot/as-of, canonical ordered Currency Signal and Pair
Signal IDs, Signal Authorization and Adoption Decision IDs, Swap and cycle-time market
evidence IDs, Position snapshot/event IDs, Account snapshot ID, selection/freshness
policy versions, checkpoint identity, and `input_snapshot_hash`. The hash includes
the canonical inputs and policy versions but excludes first-write audit `captured_at`.
Retry reads this row and never reruns input selection. Late/backfilled/corrected data
cannot alter a historical slot. Each retry instead appends a `paper_cycle_attempt`
with worker/process, resumed stage, outcome/failure, and audit timestamps; none of
those fields changes the slot or input identity.

Creating a Paper order first-writes exactly one `paper_fill_evaluation_plan` for its
approved intent. A unique approved-intent reference freezes original Decimal
quantity, Pair/side, fill and Step-schedule policies, market-selection policy,
spread/slippage/liquidity/partial-fill/cancellation/expiry versions, initial and
terminal boundaries, maximum Step count, and seed root. Audit `created_at` is
preserved but excluded from semantic identity. Retry cannot insert a conflicting
second plan.

One plan owns one or more ordered `paper_fill_evaluation_step` rows. The future schema
must enforce uniqueness of `(fill_evaluation_plan_id, step_ordinal)`. Each Step stores
its contiguous ordinal, frozen market-window start/due boundary,
`remaining_quantity_before`, exact Step selection/fill versions, derived seed, and
first-write `created_at`. Step identity excludes audit time. Step 0 is initial; Step
N can exist only after Step N-1 produced a positive partial Fill, left a positive
remainder, and policy permits continuation. No ordinal skip or duplicate is allowed.

`paper_fill_evaluation_attempt` is repeatable append-only audit. A pre-due evaluation
with no eligible evidence writes `PENDING_NO_ELIGIBLE_MARKET`, eligible count,
diagnostic, worker identity, and evaluation/audit times. It neither claims nor updates
terminal Step state. Multiple PENDING attempts may exist for one Step, and a later
pre-due attempt may resolve that same Step from eligible evidence.

A Step has zero terminal resolutions while unresolved and exactly one after
resolution. The terminal variant is selected market, no-market, cancelled, or
expired. The physical schema may finalize table names in Milestone 3, but it must
provide one cross-variant unique resolution claim for `fill_evaluation_step_id`;
separate per-table unique constraints cannot permit both a selection and another
terminal outcome. Conflicting second-write fails without partial rows.

`paper_market_observation_selection` is at most one per Step and is written before
its Fill. Eligibility requires exact Pair, valid UTC timestamps, receipt inside the
frozen Step window and no later than `evaluation_due_at`, local availability by
evaluation, configured freshness, and no provider timestamp after local receipt or
the boundary. An observation selected by an earlier Step cannot be selected again.
Research `ForwardResult` is a different evidence type and cannot be referenced.
Selection is exactly:

```sql
ORDER BY received_at ASC, provider_timestamp ASC, market_observation_id ASC
LIMIT 1
```

Retry reuses the persisted selection for that Step. A newer observation may be used
only by a later Step when it satisfies that Step's frozen window; it cannot replace
an earlier selection.

`paper_step_terminal_outcome` stores versioned no-market/cancelled/expired evidence.
Under `fill-no-market-v1`, `evaluated_at < evaluation_due_at` can create only a PENDING
attempt. At/after due, absence of an observation locally received within the Step
window creates terminal `REJECTED_NO_MARKET_EVIDENCE` or the exact policy-defined
terminal code. A late-processed observation may be selected only before terminal
first-write and only when its persisted receipt proves pre-due availability. No
PENDING attempt is updated into terminal evidence, and no terminal no-market outcome
can later become a selection.

One `paper_market_observation_selection` creates at most one `paper_fill`. Fill
identity includes exact intent/plan/Step/selection, Decimal quantity and price,
spread/slippage evidence, fill/model versions, and explicit Step seed. Quantity must
satisfy `0 < fill_quantity <= remaining_quantity_before`; zero creates no Fill and
the sum of ordered Fills cannot exceed plan original quantity. Remaining quantity is
reconstructed exactly from original quantity minus persisted ordered Fills, never
from external mutable state. A positive remainder after Fill permits only the next
contiguous Step under policy. Terminal order state prevents any new Step.

Fill outputs append after the frozen cycle input and never rewrite or become that
slot's input. Research Forward Result is never Paper fill evidence.

Paper cycle/order/fill lineage persists execution authority independently from
Adoption authorization. `SHADOW_NOT_SUBMITTED` and `PAPER` both use existing
`RuntimeMode.SHADOW`; `LIVE` maps to `RuntimeMode.LIVE` only for ExecPlan 0007. No
`RuntimeMode.PAPER` row or reinterpretation of an existing authorization is required.

Paper swap evidence adds unit basis, settlement currency, source/source version,
effective period, capture time, applicable rollover date, and content identity to the
existing availability semantics. Accrual references exact position/evidence and an
accrual formula version. Missing/stale swap is a diagnostic, not numeric zero.

Paper account and PnL use `Decimal` for money, quantity, price, margin, and cash flow.
Cash, realized/unrealized PnL, accrued swap, equity, used/available margin, gross
exposure, open positions, and open orders carry exact input lineage and formula
versions.

## Live adoption records

The Live database adds numbered migrations and append-only tables for:

```text
live_research_validation_evidence_snapshots
live_strategy_adoption_policies
live_strategy_adoption_decisions
live_signal_authorizations
live_candidate_signal_authorizations
```

Evidence snapshots retain assessment/report/run IDs, Research policy version/hash and
payload, exact cohort payload/hash, opaque metric and condition payloads, Evaluation
input snapshot version/hash/payload, source contract version, Research timestamps, and
import time. Runtime never resolves these identities through a current Research
database.

Strategy adoption policy versions are immutable content identities. Reusing a version
with different content is rejected. Approvals and revocations are separate records;
revocation references one exact approval. Evidence, policy, decision, authorization,
and Candidate authorization lineage reject UPDATE and DELETE. Identical imports,
approvals, revocations, and authorizations reuse deterministic semantic identities.

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
