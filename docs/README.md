# Design Index

## ExecPlan 0005 adoption boundary

- `exec-plans/0005-validated-signal-live-adoption.md`
  - Exact Research evidence snapshot, explicit Strategy adoption, runtime
    authorization, Candidate lineage, and shadow proof.
- `adr/0007-validated-research-evidence-requires-explicit-live-adoption.md`
  - Research validation is evidence; only a separate Live decision grants bounded
    Strategy-input authority.

## Foundation

- `PROGRAM.md`
  - ExecPlan 0001〜0005のProgram roadmapと責務連鎖。

- `00_VISION.md`
  - なぜResearchとSwap Botを刷新するか。
  - システムが最適化するもの。

- `01_ARCHITECTURE.md`
  - Layer、依存方向、Research/Live境界。

- `02_DOMAIN_MODEL.md`
  - Observation、Feature、Signal、Currency、Pair、Horizonの契約。

- `03_SIGNAL_AND_RESEARCH.md`
  - Ex-ante Signal、Forward Observation、評価指標。

- `04_SWAP_BOT.md`
  - Strategy、Portfolio、Risk、Executionの責務。

- `05_DATA_AND_VERSIONING.md`
  - immutable signal、時刻、version、データ系統。

- `06_REPOSITORY_STRUCTURE.md`
  - 推奨モノレポ構成とパッケージ責務。

- `07_ENGINEERING_STYLE.md`
  - How / What / Why / Why not規約。

- `08_TEST_STRATEGY.md`
  - Layer別の保証内容とテスト方針。

- `09_MIGRATION_STRATEGY.md`
  - 現行Swap Botからの段階移行。

## Architecture Decision Records

- `adr/0001-research-and-live-are-siblings.md`
- `adr/0002-currency-first-signal-model.md`
- `adr/0003-ai-is-a-feature-producer.md`
- `adr/0004-signals-are-immutable.md`
- `adr/0005-shared-sqlite-signal-store.md`
- `adr/0006-gmo-fx-is-primary-forward-market-source.md`
- `adr/0007-validated-research-evidence-requires-explicit-live-adoption.md`

## Reading route by task

### News scoring変更

`02_DOMAIN_MODEL.md` -> `03_SIGNAL_AND_RESEARCH.md` -> `05_DATA_AND_VERSIONING.md`

### Strategy変更

`02_DOMAIN_MODEL.md` -> `04_SWAP_BOT.md` -> `08_TEST_STRATEGY.md`

### Broker/API変更

`01_ARCHITECTURE.md` -> `04_SWAP_BOT.md` -> `06_REPOSITORY_STRUCTURE.md`

### 大規模刷新

全Foundation docs -> ADR -> `PLANS.md`

## Current implementation

- `packages/fx_core`: Research/Live共有のimmutable domain contract。
- `packages/fx_signal_store`: append-only SQLite Signal/lineage reference store。
- `apps/fx_research`: Fed/BOJ operational News collection、Feature/Signal production、
  Primary GMO FX Public M1 BID evidenceを用いたForward Result one-shot経路、strict cohort
  Research metrics、append-only Evaluation Report、明示policyによるResearch Validation。
  OANDAはoptional experimental adapterとして維持する。
- `apps/swap_bot`: Live固有のPortfolio/Risk/approved Execution境界とshadow runner。
- `docs/exec-plans/0001-shared-domain-foundation.md`: 完了済みの共有domain基盤計画。
- `docs/exec-plans/0002-operational-news-ingestion.md`: operational News ingestion計画。
- `docs/exec-plans/0003-forward-signal-evaluation.md`: Signal単位のforward observation計画。
- `docs/exec-plans/0004-signal-validation-framework.md`: strict cohort評価とResearch
  validation計画。
