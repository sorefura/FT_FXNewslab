# Repository Instructions

## Mission

このリポジトリは、FXニュース研究基盤とSwap Botを同一のドメイン思想で構築する。

最適化対象は短期的な実装速度ではない。
再現性、追跡可能性、検証可能性、責務境界を優先する。

## Context routing

作業前に `docs/README.md` を読み、対象領域の設計書を確認すること。

ルートに `TASK.md` がある場合は、セッション履歴を信用せず、作業開始前に
`TASK.md`、`HANDOFF.md`、`REVIEW.md`、`DECISIONS.md` を読むこと。これらは
引継ぎ入口であり、frozen design、ADR、ExecPlan、Phase Gateの状態を上書きしない。

大規模機能、アーキテクチャ変更、複数パッケージにまたがるリファクタリングでは、`PLANS.md` に従ってExecPlanを作成・更新すること。

関連Skillがある場合は `.agents/skills/` のSkillを使用すること。

## Phase Goal workflow

MilestoneをM単位で設計し、B単位で実装・テスト・独立レビューする作業では
`.agents/skills/run-phase-loop/` を使用する。

- `/goal` のprimary agentは進行管理を担当する。
- 書き込み可能な実装agentは同時に一つだけとする。
- 各review attemptでは新しいread-only reviewer threadを作成する。
- 以前のreviewerへfollow-upして再利用しない。
- Phase設計と最終reviewは`gpt-5.6-sol` high、B reviewは`gpt-5.6-terra` mediumを通常値とする。
- xhighは`.agents/skills/run-phase-loop/SKILL.md`の高リスク条件を満たす単発agentにだけ使用する。
- frozen design、phase state、review bundleを手作業で変更しない。
- `phase_gate.py assert-complete`が成功するまでGoalを完了扱いにしない。
- commit、push、merge、deployは明示的な許可なしに行わない。

## Windows Unicode path and encoding

このリポジトリは日本語を含むWindowsパスに置かれる場合がある。

- PowerShellではパスを文字列連結せず、-LiteralPathを優先する。
- subprocess、Git、Pythonへ渡すパスをASCII限定と仮定しない。
- PowerShellのコンソール入出力は明示的にUTF-8へ合わせる。
- repository textは原則UTF-8とし、BOMは消費側契約に合わせる。
  Python、TOML、JSONへ一律にBOMを付けない。Windows PowerShell 5.1で直接
  実行する非ASCII入り.ps1など、BOMが必要なconsumerにはUTF-8 BOMを使う。
- 仮想環境launcherが埋め込み日本語パスを扱えない場合、失敗をテスト失敗と
  混同せず、依存関係を確認したUnicode対応Python runtimeを使う。
- 生成物は文字コード、先頭BOM、Unicodeパスからのread/parse/executeを確認する。

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
