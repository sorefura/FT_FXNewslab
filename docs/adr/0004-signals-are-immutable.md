# ADR-0004: Signals Are Immutable Ex-Ante Records

## Status

Accepted

## Context

Forward Resultや市場結果を確認した後にSignal scoreを更新すると、過去時点で利用可能だった仮説を再現できなくなる。

研究結果が良く見える方向へhistorical recordが変質する危険がある。

## Decision

Signalは作成時点でimmutableとする。

修正されたscorer/model/promptは新Signalまたは新versionのSignal生成結果を作る。

Forward ResultはSignalを参照する別recordとして保存する。

## Consequences

Positive:

- Forward evaluationの再現性が保たれる。
- model/scorer version比較が可能。
- future leakageを発見しやすい。

Negative:

- record数が増える。
- correction workflowが必要。

## Why not

最新scoreでhistorical Signalを上書きしない。

それは過去仮説の改善ではなく、評価対象そのものの改変になるため。
