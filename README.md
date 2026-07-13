# FX System Design Package

このパッケージは、FXニュース研究基盤とSwap Botを同一の設計思想で再構築するための設計入力である。

目的は、Codexに「既存コードを局所改修する」のではなく、以下のシステム境界を理解させたうえで段階的な刷新を行わせることにある。

```text
Observation
    ↓
Feature
    ↓
Signal
   / \
  /   \
Research Strategy
          ↓
       Portfolio
          ↓
         Risk
          ↓
      Execution
```

## 最初に読む順序

1. `AGENTS.md`
2. `docs/00_VISION.md`
3. `docs/01_ARCHITECTURE.md`
4. `docs/02_DOMAIN_MODEL.md`
5. 対象作業に応じた設計書
6. 大規模変更の場合は `PLANS.md`

## 主要ディレクトリ

- `docs/`: システム設計と判断根拠
- `docs/adr/`: 変更しにくいアーキテクチャ判断
- `.agents/skills/`: Codex向けの再利用可能な作業手順
- `AGENTS.md`: 常時適用する短い作業規約
- `PLANS.md`: 大規模変更用ExecPlan規約
- `CODEX_BOOTSTRAP_PROMPT.md`: 初回投入用プロンプト

## 基本思想

ResearchとLive Tradingは兄弟アプリである。

ResearchがLiveの下請けではなく、LiveがResearchの実験コードを直接呼び出すこともない。

共有するのは、十分に安定したドメイン概念とSignal契約である。

```text
fx_core
├── Observation
├── Feature
├── Signal
├── Currency
├── Pair
└── Horizon

fx_research
├── Collect
├── Score
├── Forward Observe
├── Evaluate
└── Validate

swap_bot
├── Strategy
├── Portfolio
├── Risk
└── Execution
```

最初の実装目標は高収益ではない。

**再現可能、追跡可能、評価可能、差し替え可能な判断系を作ること。**

## ExecPlan 0001 implementation

Shared Domain FoundationはPython 3.11以上を対象とする。

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest -q
.venv\Scripts\python -m ruff check .
.venv\Scripts\python -m mypy packages apps
```

実注文を行わないoffline shadow一周期:

```powershell
.venv\Scripts\python -m swap_bot shadow-once --fixture tests\fixtures\shadow_cycle.json
```

期待される結果は`order_status=NOT_SUBMITTED`かつ`broker_submit_calls=0`である。
