# ADR-0003: AI Is a Feature Producer, Not a Strategy

## Status

Accepted

## Context

LLMへNewsを入力し、BUY/SELLや注文actionを直接返させる設計は実装が容易である。

しかし、判断根拠、version差、failure mode、再評価単位が粗くなる。

## Decision

AI/LLMはFeature Producerとして扱う。

```text
News Observation
      ↓
LLM Feature Producer
      ↓
Structured Feature
      ↓
Deterministic/Versioned Scorer
      ↓
Signal
```

LLMは原則としてevent type、currency relevance、factor direction、confidence等を構造化する。

最終Trade CandidateはStrategyが生成する。

## Consequences

Positive:

- LLM provider/modelを差し替えやすい。
- FeatureとScoringを独立評価できる。
- Live actionをdeterministic policyで追跡しやすい。

Negative:

- schema designが必要。
- direct action promptより実装量が増える。

## Why not

AIをStrategyとして扱わない。

LLM outputの変化をStrategy policy変更と区別できなくなるため。
