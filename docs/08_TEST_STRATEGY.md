# Test Strategy

## Production Strategy M2-A contract tests and Paper target tests

Milestone 2-A tests already state:

- execution authority is separate from Adoption runtime; Shadow/Paper map to
  `RuntimeMode.SHADOW`, Live maps to `RuntimeMode.LIVE`, and the 0006 guard rejects
  Live;
- exact `USD_JPY`/`MXN_JPY` config is immutable, has no hidden defaults, and has a
  deterministic canonical identity with exact integer-microsecond durations;
- versioned `OperationalSwapEvidence` validates its intrinsic content ID, UTC
  availability/effective time, availability/value rules, and received sign without
  modifying accepted `SwapQuote`;
- production entry accepts `AuthorizedSignal`, not raw Signal, and produces a
  deterministic Candidate-or-structured-skip result with exact authorization,
  adoption, Swap, and config lineage;
- `ProductionTradeCandidate` stores lossless `PairScore` separately from confidence
  and has no quantity, Portfolio, Risk, or Broker fields;
- `PositionCloseCandidate` is a distinct, quantity-free, always-reduce-only ordinary
  close request and is not Risk emergency liquidation; and
- the strategy package imports no Research, AI/LLM, Execution, Portfolio, Risk, or
  Broker module, while no concrete production Strategy or migration exists yet.

Later ExecPlan 0006 implementation tests will state the following guarantees:

- exact Authorized Signals and immutable Strategy config produce deterministic
  Candidates or structured skips;
- deterministic per-Pair evaluation does not silently lose a second eligible Pair;
- `USD_JPY`/`MXN_JPY` is the exact configured Pair set and expansion changes config
  identity;
- Pair direction uses the shared stored `currency-pair-v1` contract, not a duplicated
  subtraction or an implicit zero for missing currency evidence;
- threshold equality is neutral, and BUY/SELL requires matching strictly positive
  fresh received swap;
- stale, missing, malformed, zero, negative, wrong-Pair, or unavailable swap cannot
  produce an entry Candidate;
- Strategy cannot decide quantity or import AI, Research evaluation, Execution, or
  Broker modules;
- ordinary reduce-only close, partial close, and Risk emergency liquidation preserve
  distinct typed lineage;
- `SHADOW_NOT_SUBMITTED`, `PAPER`, and `LIVE` are distinct, and 0006 rejects `LIVE`
  before a cycle starts;
- only approved intents can create Paper orders;
- deterministic fill replay uses exact post-intent available market evidence and
  never Research Forward Results or future observations;
- legal order lifecycle, Decimal ledger balance, PnL, swap accrual, and reconciliation
  are preserved by append-only persistence;
- crash injection at every intent/order/fill/ledger/cycle boundary converges without
  duplicate semantic records;
- a Paper composition-root tripwire observes zero real Broker transport construction
  and submit calls; and
- burn-in reporting cannot create Live authority or alter two-step arming.

Paper results are asserted from observable events/ledger/probes. They are not proven
by hardcoded `paper_executed` or Broker-call summary fields.

### Authority mapping target tests

- `SHADOW_NOT_SUBMITTED` maps to Adoption `RuntimeMode.SHADOW`.
- `PAPER` maps to Adoption `RuntimeMode.SHADOW`.
- `LIVE` maps to Adoption `RuntimeMode.LIVE`, while the ExecPlan 0006 composition
  rejects it before Signal authorization or cycle claim.
- A `SHADOW_ONLY` approval is usable for Paper, as is `LIVE_ELIGIBLE`, without
  creating Live Broker authority.
- Paper cycle/order/fill lineage retains `ExecutionAuthorityMode.PAPER` separately
  from the SHADOW Signal authorization.
- A Paper result cannot create `RuntimeMode.LIVE` authorization, Live rollout
  authority, or real Broker construction/submission.

### Cycle first-write target tests

- The same schedule slot, as-of, authority, Strategy/config, and cycle-policy version
  always resolve one `CycleSlotId`, regardless of discovered input rows.
- A late Signal cannot create another logical cycle after the first claim.
- Late Authorization and Adoption Decision rows cannot alter the persisted input
  snapshot.
- Newer SwapQuote and cycle market evidence cannot alter the persisted input
  snapshot.
- Newer Account or Position evidence cannot alter the persisted input snapshot.
- A conflicting second input snapshot for one slot is rejected atomically and leaves
  the original row unchanged.
- Retry creates only a new `CycleAttempt` and reuses the exact frozen input snapshot.
- First-write `captured_at`, checkpoint, canonical input hash, selected IDs, and
  policy-version metadata remain unchanged across retry.
- Cycle, order, fill, ledger, and swap semantic IDs exclude attempt wall-clock and
  worker metadata.
- Tests never depend on database natural order or an implicit latest-row selection.

### Fill cardinality and partial-fill target tests

- One approved intent resolves exactly one `FillEvaluationPlan`, while one Plan may
  own ordered contiguous Steps.
- Original quantity 1000 and a Step 0 Fill of 400 produces `PARTIALLY_FILLED` with
  remaining quantity 600.
- Step 1 records `remaining_quantity_before == 600`, may select a different quote,
  and a Fill of 600 produces final `FILLED` with total Fill exactly 1000.
- The two Steps have ordinals exactly `0, 1`; a positive partial Fill is the only
  fill result that can permit the next Step.
- A resolved Step has exactly one terminal variant, while `PARTIALLY_FILLED` remains
  nonterminal for the order.

### Fill idempotency target tests

- Retry after Step 0 Fill does not duplicate that Fill.
- Restart after partial fill resumes/reuses Step 1 rather than recreating Step 0 or
  inventing another ordinal.
- `(plan_id, step_ordinal)` is unique; one Step cannot persist two market selections
  or two cross-variant terminal resolutions.
- One selection cannot persist two Paper Fills.
- Retry cannot move Step due/window or expiry boundaries or change plan/Step seed,
  quantity, or policy/model versions.
- A conflicting second plan, Step, terminal resolution, selection, or Fill fails
  closed without partial rows.

### PENDING and terminal no-market target tests

- No quote before due appends immutable
  `PENDING_NO_ELIGIBLE_MARKET` attempt evidence.
- PENDING is not a terminal Step resolution and does not consume its terminal claim.
- After one or more PENDING attempts, a quote received before due can resolve the same
  Step with a market selection.
- Multiple PENDING attempts do not change Step identity and none is updated into a
  selection or no-market row.
- At/after due with no eligible pre-due receipt, the Step appends the versioned
  terminal `REJECTED_NO_MARKET_EVIDENCE` or exact policy-defined no-market outcome.
- Once terminal no-market evidence exists, no later or late-processed quote can turn
  that Step into a selection or Fill.

### Fill quantity target tests

- Fill quantity cannot exceed `remaining_quantity_before` and zero quantity creates
  no Paper Fill.
- Sum of ordered Fill quantities never exceeds original approved quantity.
- Remaining quantity is exactly reproducible from original quantity and persisted
  ordered Fills, with Decimal exactness.
- Mutable external current quantity cannot reinterpret a historical Step or Fill.

### Fill terminal-state target tests

- `FILLED`, `CANCELLED`, `EXPIRED`, and `REJECTED` prohibit creation of another Step.
- Only `PARTIALLY_FILLED`, positive remaining quantity, and explicit policy
  permission can lead to the next contiguous Step.
- Maximum-Step and Plan-expiry boundaries lead to versioned terminal evidence and do
  not create another Step.
- Cancellation/expiry racing an unresolved Step follows the frozen precedence-policy
  version and appends evidence without UPDATE-based transition.

### Per-Step market selection target tests

- Each Step may select distinct market evidence inside its own frozen window.
- Multiple valid quotes select by `received_at ASC`, provider timestamp ASC, then
  market-observation ID ASC for that Step.
- Identical receipt/provider timestamps use market-observation ID as the deterministic
  tie-breaker.
- Retry never reselects an already resolved Step, and a newer quote cannot replace
  that Step's persisted selection.
- A later Step can select a new quote only when it satisfies that Step's window and
  has not already been selected by an earlier Step.
- A quote before intent/window start or after Step due is rejected.
- A future provider timestamp, malformed observation, wrong Pair, or observation not
  locally available by evaluation is rejected.
- A Research `ForwardResult` cannot satisfy the Paper fill input contract.
- Market selection is persisted before its zero-or-one Paper Fill and reused after
  restart.

## Strategy adoption tests

ExecPlan 0005 tests state What each authority boundary guarantees:

- explicit assessment-ID reads validate full Research lineage and perform no Research
  writes;
- EXPERIMENTAL/PROMISING, missing evidence, bad hashes, malformed JSON, and unknown
  snapshot contracts fail closed;
- dry-run creates no Live database and `--apply` is atomic and idempotent;
- forged evidence IDs, contract versions, lineage IDs, condition results, and input/
  metric/cohort/policy payload-hash pairs fail before any adoption row is written;
- advancing-clock Approval/Revocation retries preserve first-write audit metadata and
  the original authority-start boundary;
- every strict cohort dimension, nullable version, mode, and bounded time is exact;
- no approval, pre-approval Signal, pre-authority authorization, expiration,
  revocation, mismatch, and ambiguity stop before Strategy with structured reason
  codes;
- Candidate persistence requires exact current authorization and leaves no partial row
  on failure;
- the full approved shadow path preserves Candidate -> Portfolio -> Risk -> intent
  lineage and records `NOT_SUBMITTED`;
- Broker submission safety is measured with an injected counting gateway. Tests assert
  its observed call count is zero and never rely on a hardcoded summary field.

Architecture tests prohibit Live imports of Research and adoption-gate imports of
Execution/Broker ports.

## Goal

テストは内部実装を固定するためではなく、Layerが保証するWhatを固定する。

テスト名とfixtureからdomain behaviorが読める状態を目標にする。

## Domain tests

高速、pure、deterministic。

対象:

- score bounds
- pair sign convention
- currency exposure decomposition
- horizon semantics
- immutable value objects
- invalid state rejection

例:

```text
USDJPY long creates positive USD and negative JPY exposure
pair score subtracts quote currency signal from base currency signal
confidence outside [0, 1] is rejected
```

## Feature producer contract tests

同じnormalized inputに対するoutput schemaとinvariantsを検証する。

LLM exact wordingやraw textをgolden assertionしない。

検証候補:

- required feature fields
- numeric ranges
- target currency mapping
- version metadata
- malformed provider output handling

LLM integrationはrecorded fixtureまたはprovider stubを基本にする。

Operational provider adapterのunit testはfake transportを使用し、外部networkへ接続しない。
provider選択、認証情報不足、malformed response、禁止action field、timeout伝播を検証する。
実OpenAI接続は`openai_smoke` markerと明示opt-in、認証情報が揃う場合だけ実行する。

Feature production CLIはitem failureをJSONの`attempted`、`completed`、`failed`で返し、
defaultでは1件以上のfailureをnon-zero exit codeにする。部分成功を成功扱いする場合は
明示optionを要求する。

## Operational News ingestion tests

通常CIではrecorded RSS、HTML、detail page、provider responseを使用する。

What:

- source configurationがFedをUSD、BOJをJPYへ割り当てる。
- repeated pollが`first_seen_at`を動かさずduplicateを作らない。
- same URLのchanged contentが新Observationになる。
- date-only metadataが架空のpublication timestampにならない。
- malformed feed、changed HTML、本文抽出失敗がneutral Signalにならない。
- bounded HTTP GET retryを超えた失敗が明示される。
- retrieval、normalization、persistenceの途中失敗を別stageとして監査できる。
- fail-fast後の再実行が、途中まで保存済みのObservationを重複させない。

公式endpointを呼ぶtestには`source_smoke` markerを付け、通常CIから分離する。

## Signal tests

What:

- Featureから期待されるSignal targetが生成される。
- Signalがsource feature idsを保持する。
- scorer versionが保存される。
- Currency-to-Pair変換の符号規約が一定。
- Signal作成後に結果評価で書き換えられない。
- Observation、Feature、Signal、production recordの時刻がavailability順になる。
- crash後に既存Featureを再利用してもSignalがFeatureより過去にならない。

## Research tests

最重要はfuture leakage防止。

What:

- upstream data availabilityはfirst_seen_atを守り、Forward observationは
  Signal.created_atより前をanchorにしない。
- horizon completion前にForward Resultをfinalizeしない。
- original Signalをmutateしない。
- 全Signalに15m/1h/4h/1d/3d jobを作る。
- first complete M1 openをt0/txに使い、5分を超えてforward-fillしない。
- incomplete candle、tx candle high/low、選択済みtxより未来のcandleを使わない。
- target returnはprojection sign、MFE/MAEはprojection後のSignal directionに従う。
- neutral directionのMFE/MAEはnullになる。
- exact MarketSnapshotからnetworkなしで同じForwardResultを再計算できる。
- provider failureとalignment unavailableとzero returnを区別する。
- version別metricsが混ざらない。
- strict cohortがSignal/Forward horizon、Signal version群、market basis、projection/formula
  versionを分離する。
- perfect/inverse/tied Spearmanをhand-calculated fixtureで検証する。
- insufficient/constant ICを0ではなくundefined reasonとして検証する。
- deterministic bootstrapが同じinput/configurationで同じintervalになる。
- Hit Rateがneutral/zero returnを除外して個別件数とWilson intervalを返す。
- fixed bucket境界、empty state、unbucketed Pair score、monotonic/non-monotonic stateを検証する。
- null MFE/MAEとquarterly insufficient sliceを明示的に数える。
- exact ordered input IDs、duplicate run reuse、新ForwardResultによる新runを検証する。
- Evaluation run/report/policy/assessmentのUPDATE/DELETEが拒否される。
- policyなしではAssessmentを作らず、AssessmentがStrategy approvalを生成しない。

OANDA adapter unit testはfake transportとrecorded responseを用い、M1 midpoint、
`smooth=false`、complete candle、Decimal OHLC、token headerを確認する。実OANDA接続は
`oanda_smoke` marker、`RUN_OANDA_SMOKE=1`、credential/base URLが揃う場合だけ実行する。

Primary GMO FX adapter unit testはfake transportとrecorded Public responseを用い、
`USD_JPY` normalization、M1 BID、UTC timestamp、Decimal OHLC、bounded provider-date split、
response-time completion、same-content deduplication、changed revision preservationを確認する。
private credentialが不要であることを境界から保証する。実Public接続は`gmo_fx_smoke`
markerと`RUN_GMO_FX_SMOKE=1`で通常CIから分離する。

統計関数は小さなhand-calculated datasetで検証する。Application/CLI testはResearch-owned
SQLite fixtureだけを使い、Strategy、Portfolio、Risk、Execution、Brokerをimportまたはinvoke
しない。

## Strategy tests

Strategy testはBrokerを使用しない。

What:

- aligned carry/fundamental signals produce candidate
- insufficient signal support produces no candidate
- incompatible horizon behavior follows policy
- candidate records contributing signal ids
- strategy config version is captured

内部weighted sumの全中間値をassertしすぎない。

score formula自体がpublic contractの場合のみ明示する。

## Portfolio tests

What:

- exposure is aggregated across pairs
- JPY short concentration is detected across USDJPY/EURJPY/GBPJPY
- accepted candidate can be resized
- rejected candidate has structured reason
- pending intents are included where policy requires
- portfolio decision identifies the candidate it evaluated

## Risk tests

Risk ruleごとに独立したWhatを表現する。

例:

```text
rejects execution when account data is stale
rejects execution when margin health is below limit
rejects duplicate idempotency key
```

RiskからPortfolio、PortfolioからCandidateへのID参照が一致しない入力は、各decisionが
単独で正当でも拒否する。

複数Risk ruleを1テストへ詰め込まない。

## Execution tests

Broker adapter contractを分離する。

What:

- ExecutionIntent maps to broker order semantics
- idempotency prevents duplicate submission
- broker error is normalized
- partial fill is preserved
- retry occurs only for explicitly retryable failures
- shadow orchestration records zero calls on an injected BrokerGateway probe

Broker非呼出しをresult用の固定値で表現しない。呼出し時にcountが増えるfakeまたはmockを
Execution境界へ注入し、その観測値で検証する。

実Broker sandbox/test APIが利用できる場合、unit testとは別suiteにする。

## Architecture tests

可能ならimport boundaryを機械検証する。

最低限検出したい。

- `fx_core` importing app modules
- strategy importing broker SDK
- research importing execution modules
- execution importing feature producer modules

## Test comments

テストコメントも原則不要。

Given/When/Thenコメントを機械的に追加しない。

Arrange構造とhelper namingで読みやすくする。

Why notに該当する制約がある場合のみcommentを許容する。
