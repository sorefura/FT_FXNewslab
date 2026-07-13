# Test Strategy

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

## Signal tests

What:

- Featureから期待されるSignal targetが生成される。
- Signalがsource feature idsを保持する。
- scorer versionが保存される。
- Currency-to-Pair変換の符号規約が一定。
- Signal作成後に結果評価で書き換えられない。

## Research tests

最重要はfuture leakage防止。

What:

- first_seen_atより前の市場情報を使わない。
- horizon completion前にForward Resultをfinalizeしない。
- original Signalをmutateしない。
- MFE/MAEの方向規約がSignal sideと一致する。
- version別metricsが混ざらない。

統計関数は小さなhand-calculated datasetで検証する。

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

## Risk tests

Risk ruleごとに独立したWhatを表現する。

例:

```text
rejects execution when account data is stale
rejects execution when margin health is below limit
rejects duplicate idempotency key
```

複数Risk ruleを1テストへ詰め込まない。

## Execution tests

Broker adapter contractを分離する。

What:

- ExecutionIntent maps to broker order semantics
- idempotency prevents duplicate submission
- broker error is normalized
- partial fill is preserved
- retry occurs only for explicitly retryable failures

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
