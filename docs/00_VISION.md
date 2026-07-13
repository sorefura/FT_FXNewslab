# Vision

## Problem

現在の構想は、ニュースを取得してスコア化し、その後の市場結果と比較するForward Scoringから始まっている。

この考え方をFXへ拡張する際、単にニュースソースを海外へ増やし、既存Swap Botへニューススコアを追加するだけでは不十分である。

理由は、以下の責務が混ざりやすいためである。

- 何が観測されたか。
- 観測をどう特徴量化したか。
- どの市場仮説が生成されたか。
- 仮説が過去に有用だったか。
- 現在その仮説をStrategyが採用するか。
- 現Portfolioで注文可能か。
- Brokerへ何を発注するか。

これらを分離しない場合、利益や損失が発生しても「どの判断が正しかったか」を機械的に説明できない。

## Vision

システムを「売買を当てるAI」ではなく、以下の判断系として構築する。

> 市場に関する観測を再現可能な特徴量と仮説へ変換し、仮説の将来有用性を研究し、検証済みのSignalをLive Strategyが独立したRisk制御下で利用する。

## System qualities

優先順位は以下とする。

1. Reproducibility
2. Traceability
3. Evaluability
4. Isolation of responsibility
5. Replaceability
6. Trading performance

Trading performanceを軽視する意味ではない。

評価不能な収益改善は維持できず、原因不明の損失悪化も修正できないため、まず判断系の品質を作る。

## Key idea

ResearchとLive Tradingは同じSignal言語を共有する。

```text
Observation
    ↓
Feature
    ↓
Signal
   / \
  /   \
Forward  Strategy
Evaluate     ↓
          Portfolio
             ↓
            Risk
             ↓
          Execution
```

ResearchはSignalの将来情報量を測定する。

LiveはSignalを直接注文へ変換せず、Strategyの入力として利用する。

## Success criteria

初期成功条件は以下である。

- 同一Observationとversion情報からSignal生成過程を追跡できる。
- Signal発生後の複数HorizonでForward Resultを収集できる。
- Signalをscore bucket、currency、event type、horizonで評価できる。
- Strategyが使用したSignal集合を追跡できる。
- PositionをPairだけでなくCurrency Exposureへ分解できる。
- Risk拒否理由がExecution結果と分離して保存される。
- LLM providerを変更してもdomain contractが変わらない。
- Brokerを変更してもStrategy contractが変わらない。

## Non-goals

初期段階では以下を目標にしない。

- 完全自律型AI trader
- LLMによる直接注文
- 全通貨、全ニュースソース対応
- 高頻度取引
- 初期からの複雑な機械学習
- 汎用トレーディングプラットフォーム化
- マイクロサービス分割

最初はモノレポ内で明確なmodule/package境界を作る。
