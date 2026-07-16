# Test Strategy

## Production Strategy and Paper target tests

ExecPlan 0006 implementation tests will state the following guarantees:

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

### Fill selection target tests

- Multiple valid quotes select by `received_at ASC`, provider timestamp ASC, then
  market-observation ID ASC.
- Identical receipt/provider timestamps use market-observation ID as the deterministic
  tie-breaker.
- A quote received before intent creation is rejected.
- A quote received after the frozen `fill_due_at` is rejected.
- A future provider timestamp, malformed observation, wrong Pair, or observation not
  locally available by evaluation is rejected.
- A Research `ForwardResult` cannot satisfy the Paper fill input contract.
- Market selection is persisted before fill evidence and reused after restart.
- A newer quote appearing after selection does not replace the selected evidence.
- No eligible quote remains `PENDING` before due and produces the versioned terminal
  `REJECTED_NO_MARKET_EVIDENCE` outcome at/after due.
- Once terminal no-market evidence exists, retry cannot turn it into a fill; a late-
  retrieved quote is usable only if persisted local receipt proves availability by
  due and no earlier selection/terminal record exists.
- Retry does not move `fill_due_at`, change a seed, or change any fill/selection/
  spread/slippage/liquidity/partial-fill policy version.

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
