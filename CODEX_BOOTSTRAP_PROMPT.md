# Initial Codex Prompt

以下をCodexの初回タスクとして使用する。

---

このリポジトリのSwap Botを、ResearchとLive Tradingを兄弟アプリとして扱う新アーキテクチャへ段階的に刷新してください。

最初にコード変更は行わず、次を実施してください。

1. ルートの `AGENTS.md` を読む。
2. `docs/README.md` から設計文書を順に読む。
3. 現行リポジトリ構造を調査する。
4. 現行コードを次の責務へマッピングする。
   - Observation
   - Feature
   - Signal
   - Strategy
   - Portfolio
   - Risk
   - Execution
   - Research
   - Infrastructure
5. 責務混在、直接依存、再現性欠如、versioning欠如を特定する。
6. `PLANS.md` に従い、刷新用ExecPlanを作成する。
7. Big bang rewriteは提案しない。動作を維持できる段階的migrationを設計する。
8. 最初のMilestoneは、既存コードを壊さずに新しい境界を導入できる最小単位にする。

特に確認すること:

- AI/LLMが直接BUY/SELLまたは注文判断を返していないか。
- News処理がStrategyやExecutionへ直接接続していないか。
- StrategyがRiskやPortfolioを迂回していないか。
- Pair単位だけでExposureを管理していないか。
- 過去Signalを再現するmodel/prompt/scorer versionが保存されているか。
- Swap値が固定overrideに依存している箇所と、動的sourceへ移行できる境界。
- Broker adapterとdomain/application logicの混在。
- Research評価コードをLive pathで使用していないか。

コーディング規約:

- Production codeにはHowを置く。
- Test codeにはWhatを置く。
- Commit logにはWhyを置く。
- Code commentにはWhy notだけを置く。
- 説明コメントを増やして理解可能性を補わない。命名、型、責務分割で表現する。
- コメントは日本語で行うこと。

調査結果とExecPlanを提示し、最初のMilestoneを実装可能な状態まで具体化してください。
