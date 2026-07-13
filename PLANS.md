# Codex Execution Plans

ExecPlanは、大規模機能または重要な刷新を、途中で文脈を失っても継続できる実行仕様として扱う。

## When an ExecPlan is required

以下のいずれかに該当する場合はExecPlanを作成する。

- `fx_core` の公開ドメイン契約を変更する。
- ResearchとSwap Botの両方へ影響する。
- 永続化スキーマを変更する。
- 既存Swap Botの責務を別Layerへ移動する。
- Signalの意味、向き、時間軸、versioningを変更する。
- 複数段階のmigrationが必要である。
- 1回の小さな差分で安全に完結しない。

## Required properties

ExecPlanはliving documentである。
実装の進行に合わせて更新する。

将来の作業者は、現在のworking treeとExecPlanだけを持つ前提で書く。

過去チャット、暗黙の合意、作成者の記憶へ依存しない。

## Required sections

### Goal

何が可能になるかを書く。

### Non-goals

今回あえて行わないものを書く。

### Current state

関連する現行コード、責務、既知の問題を書く。

### Target architecture

変更後の責務とデータフローを書く。

### Invariants

変更中も絶対に壊してはいけない条件を書く。

### Milestones

独立して検証可能な単位へ分ける。

各Milestoneは以下を持つ。

- deliverable
- files/modules expected to change
- observable behavior
- verification command

### Migration and compatibility

既存データ、設定、API、ジョブ、注文処理への影響を書く。

### Validation

テスト、型チェック、Lint、必要なdry-runを具体的なコマンドで書く。

### Decision log

実装中に生じた重要判断を日付付きで追記する。

### Progress

完了、進行中、残作業を更新する。

## Planning style

実装コードを大量に先書きしない。

ファイル名、型名、責務、データフロー、検証可能な結果を中心にする。

不確実な箇所は仮定として明示し、実装調査後にPlanを更新する。

## Completion

全Milestone完了後、設計との差異を確認する。

意図的な差異はdocsまたはADRへ反映する。

ExecPlanは履歴として残してよいが、恒久ルールをExecPlanだけに残さない。
