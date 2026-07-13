# Engineering Style

## Information placement

本プロジェクトでは、説明の重複を避けるため情報の置き場所を分ける。

| Location | Question |
|---|---|
| Production code | How |
| Test code | What |
| Commit log | Why |
| Code comment | Why not |

## Production code: How

コードは「どう実現するか」を表現する。

理解可能性は以下で作る。

- precise naming
- domain types
- small cohesive functions
- explicit input/output
- module boundaries

説明コメントで複雑な関数を正当化しない。

複雑な関数は責務を分割する。

### Preferred

```python
fresh_signals = signal_freshness_policy.filter(signals, now)
candidate = strategy.evaluate(fresh_signals, carry_snapshot)
```

### Avoid

```python
# First, filter old signals because old signals should not be used.
signals = [...]
# Then calculate strategy score.
score = ...
```

## Test code: What

テストは保証する振る舞いを宣言する。

テスト名はdomain languageを使う。

### Preferred

```python
def test_rejects_candidate_when_jpy_short_exposure_exceeds_limit():
    ...
```

```python
def test_forward_result_does_not_mutate_original_signal():
    ...
```

### Avoid

```python
def test_calculate_1():
    ...
```

テストがproduction algorithmを再実装しない。

入力と観測可能な結果でWhatを示す。

## Commit log: Why

Commit subjectはimperativeまたは簡潔な変更意図にする。

本文が必要な場合は、変更理由と設計上の意味を書く。

### Preferred

```text
Separate portfolio exposure checks from strategy scoring

JPY concentration was being treated as a score penalty, which allowed
a sufficiently strong strategy score to bypass a portfolio constraint.
Move concentration handling to PortfolioDecision so exposure rejection
is explicit and auditable.
```

### Avoid

```text
Update strategy.py and add portfolio.py

- add class
- rename method
- update imports
- add tests
```

diffから読める内容をcommit messageで繰り返さない。

## Code comment: Why not

コメントはrare exceptionとする。

許容する主な状況:

- obvious alternativeが危険
- external API constraint
- temporal ordering requirement
- historical reproducibility constraint
- intentionally disabled optimization
- counterintuitive business rule

### Preferred

```python
# Do not fall back to zero: an unknown swap value must block carry evaluation.
if swap is None:
    raise SwapDataUnavailable(...)
```

### Preferred

```python
# Keep first_seen_at for evaluation; published_at may predate actual ingestion.
evaluation_time = observation.first_seen_at
```

### Avoid

```python
# Check if swap is None.
if swap is None:
    ...
```

## Docstrings

Docstringはcomment volumeの抜け道にしない。

Public API/Protocolで、型だけでは表せないcontractがある場合に使用する。

例:

- unit
- sign convention
- idempotency guarantee
- external side effect
- exceptional behavior

private helperには原則不要。

## Naming

`manager`, `processor`, `handler`, `util`, `helper`を安易に使わない。

責務を名前にする。

例:

- `SignalFreshnessPolicy`
- `CurrencyExposureCalculator`
- `ForwardResultEvaluator`
- `BrokerOrderGateway`

## Abstraction

3回重複したら必ず抽象化、というルールは採用しない。

同じ意味を持つ重複かを確認する。

Researchの`Score`とStrategyの`Score`が同じfloatでも、意味が異なるなら共通基底型へ統合しない。

## Type discipline

Domain上の意味が異なる値は可能な範囲で型を分ける。

特に注意する。

- score vs confidence
- price vs quantity
- percent vs ratio
- pips vs bps
- source timestamp vs observed timestamp
- broker order id vs internal execution id

## Logging

ログはcommentの代替ではない。

structured contextを優先する。

例:

```text
event=risk_decision
candidate_id=...
decision=reject
reason=max_currency_exposure
currency=JPY
current_exposure=...
proposed_exposure=...
limit=...
```

秘密情報、API secret、認証payloadは記録しない。
