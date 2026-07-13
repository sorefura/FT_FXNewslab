# ADR-0002: Fundamental Signals Are Currency-First

## Status

Accepted

## Context

Newsを最初からUSDJPY、EURUSD等のPair scoreへ変換すると、一つのUSD newsをPairごとに再評価する必要がある。

また、USD判断とJPY判断のどちらが誤っていたかを分解しにくい。

## Decision

Fundamental News Signalは原則Currency targetとして生成する。

Pair Signalはbase minus quoteの明示的transformationで導出する。

```text
PairScore(A/B) = CurrencyScore(A) - CurrencyScore(B)
```

transformation versionを保存する。

## Consequences

Positive:

- News evaluationを複数Pairへ再利用できる。
- Currency単位でSignal qualityを評価できる。
- Portfolio Exposure modelと整合する。

Negative:

- Pair固有イベントの扱いに例外が必要。
- 通貨間相互作用を単純差分だけでは表現できない場合がある。

## Why not

すべてをPair direct scoreにしない。

Signal attributionとcross-pair再利用性を失うため。

Pair固有Signalは例外として許容する。
