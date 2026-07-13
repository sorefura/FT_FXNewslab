# ADR-0001: Research and Live Trading Are Sibling Applications

## Status

Accepted

## Context

Forward Scoring研究とSwap Botは、News、Market Data、Signal概念を共有する。

ResearchをSwap Bot内部の分析moduleにすると、統計評価とLive order lifecycleが混在する。

逆にSwap BotがResearch pipelineを直接importすると、実験的変更がLive pathへ漏れる。

## Decision

`fx_research`と`swap_bot`を兄弟Applicationとして分離する。

共通domain vocabularyのみ`fx_core`で共有する。

```text
fx_core
  ↑     ↑
research live
```

ResearchとLiveは互いをimportしない。

## Consequences

Positive:

- Research experimentがLive executionへ直接影響しない。
- Signal contractを中心に比較できる。
- 独立したtest strategyを持てる。

Negative:

- 一部のorchestration codeが似る可能性がある。
- 共通化判断を慎重に行う必要がある。

## Why not

ResearchをLive Bot内のsubmoduleにしない。

研究の評価周期、データ保持、failure toleranceはLive executionと異なるため。
