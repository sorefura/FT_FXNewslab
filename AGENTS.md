# Repository Instructions

## Mission

このリポジトリは、FXニュース研究基盤とSwap Botを同一のドメイン思想で構築する。

最適化対象は短期的な実装速度ではない。
再現性、追跡可能性、検証可能性、責務境界を優先する。

## Context routing

作業前に `docs/README.md` を読み、対象領域の設計書を確認すること。

大規模機能、アーキテクチャ変更、複数パッケージにまたがるリファクタリングでは、`PLANS.md` に従ってExecPlanを作成・更新すること。

関連Skillがある場合は `.agents/skills/` のSkillを使用すること。

## Core boundaries

データフローは原則として次を守る。

`Observation -> Feature -> Signal`

Live Tradingは次を守る。

`Signal -> Strategy -> Portfolio -> Risk -> Execution`

Researchは次を守る。

`Signal -> Forward Observation -> Evaluation -> Validation`

禁止事項:

- AI/LLMから直接注文を生成しない。
- Newsから直接BUY/SELLを生成しない。
- StrategyからBroker APIを直接呼ばない。
- Risk判定をExecution内部へ隠さない。
- Research評価結果で過去のSignalを書き換えない。
- Pair単位の判断だけで通貨Exposureを無視しない。
- Live固有型を `fx_core` に持ち込まない。
- Research固有統計を `fx_core` に持ち込まない。

## Engineering writing rule

情報の置き場所を次で分離する。

- Production code: **How**
- Test code: **What**
- Commit log: **Why**
- Code comment: **Why not**

### Production code = How

コードは処理方法を構造で示す。
命名、型、関数分割、モジュール境界で読み取れる実装を優先する。

コード内に仕様説明や経緯説明を大量に書かない。

### Test code = What

テスト名とテスト構造は、システムが何を保証するかを示す。

実装手順を再説明しない。
内部アルゴリズムのコピーを期待値生成に使わない。

### Commit log = Why

コミットメッセージは変更理由、解決する問題、意図した設計変化を書く。

変更ファイル一覧や処理手順の列挙を主目的にしない。

### Code comment = Why not

コメントは、自然に見える別案を採用しなかった理由、外部制約、危険な最適化、順序依存などを残す場合に限定する。

コードが何をしているかを逐語的に説明するコメントは禁止する。

悪い例:

```python
# scoreを計算する
score = calculate_score(features)
```

許容例:

```python
# Do not reuse the latest model version here; historical signals must remain reproducible.
model = model_registry.get(signal.model_version)
```

## Docstrings

公開境界の契約が型と命名だけでは不十分な場合のみ使用する。

Docstringを実装解説の置き場にしない。
非公開関数へ機械的にDocstringを追加しない。

## Change discipline

既存設計と異なる実装が必要な場合、黙って境界を破らない。

1. 該当設計書とADRを確認する。
2. 変更理由を明示する。
3. 必要ならADRを追加する。
4. テストで新しい保証内容を表現する。

## Definition of done

変更完了には最低限以下を含む。

- 対象の設計境界を維持している。
- 型チェック、Lint、対象テストを実行している。
- 新しい振る舞いはテスト名からWhatが読める。
- コメント追加はWhy notに該当する。
- 永続化形式を変更した場合はversioning/migrationを考慮している。
- Signal生成変更では再現性とmodel/prompt/scorer versionを確認している。
- Strategy変更ではPortfolio/Riskを迂回していない。
- 設計判断が変わった場合はdocsまたはADRを更新している。

## Default implementation posture

不明点がある場合、巨大な抽象化を先に作らない。

現在必要な最小境界を実装し、将来差し替えたい箇所はProtocolまたは明確なApplication Portとして切る。

「汎用性のため」だけの基底クラス、Factory、Registry、Plugin機構を増やさない。

ただしBroker、LLM provider、Market data source、Clock、ID generator、Persistenceは外部境界として差し替え可能にする。
