# Repository Structure

## Current adoption modules

ExecPlan 0005 keeps Research-specific validation types out of `fx_core`. The Live
application owns:

- `adoption.py`: immutable evidence, policy, decision, authorization, and exact cohort
  contracts.
- `research_evidence.py`: Live-owned Port plus read-only Research SQLite adapter.
- `adoption_application.py`: explicit approve/revoke one-shot orchestration.
- `adoption_store.py` and `migrations/`: append-only Live state.
- `adoption_gate.py`: Live-only runtime authorization.
- `authorized_shadow.py`: shadow composition with existing Portfolio/Risk/Execution.

Research application code remains unchanged and has no Live import.

## Recommended monorepo

```text
fx-system/
├── AGENTS.md
├── PLANS.md
├── pyproject.toml
├── docs/
│   ├── README.md
│   ├── ...
│   └── adr/
├── packages/
│   └── fx_core/
│       ├── pyproject.toml
│       └── src/fx_core/
│           ├── observation/
│           ├── feature/
│           ├── signal/
│           ├── market/
│           ├── currency/
│           └── time/
├── apps/
│   ├── fx_research/
│   │   ├── AGENTS.md
│   │   ├── pyproject.toml
│   │   └── src/fx_research/
│   │       ├── collection/
│   │       ├── scoring/
│   │       ├── forward/
│   │       ├── evaluation/
│   │       ├── validation/
│   │       └── infrastructure/
│   └── swap_bot/
│       ├── AGENTS.md
│       ├── pyproject.toml
│       └── src/swap_bot/
│           ├── strategy/
│           ├── portfolio/
│           ├── risk/
│           ├── execution/
│           ├── application/
│           └── infrastructure/
└── tests/
```

実際の既存repo構造に合わせて段階移行する。

最初から全file moveを要求しない。

Operational ingestionの初期実装では`apps/fx_research`がcollection、normalization、
feature production、application orchestrationを所有する。Federal ReserveとBank of Japanの
parser、HTTP、PDF、SQLite polling stateはinfrastructureとして隔離し、`fx_core`へ持ち込まない。

## fx_core admission rule

型やロジックを`fx_core`へ置く条件:

- ResearchとLiveの両方で同じ意味を持つ。
- Broker/LLM/DB framework固有ではない。
- Strategy固有ではない。
- Research evaluation固有ではない。
- 安定したdomain vocabularyである。

迷った場合は利用側appへ置く。

2箇所に似たcodeが存在することより、早すぎるshared abstractionの方を警戒する。

## fx_research

所有するもの:

- News/market collection orchestration
- Feature/Signal experiment pipelines
- Forward observation
- Evaluation
- Statistical metrics
- Signal validation lifecycle
- research datasets

所有しないもの:

- Broker order
- account margin control
- live position sizing
- live execution retry

## swap_bot

所有するもの:

- Strategy
- Portfolio
- Live risk
- Execution orchestration
- Broker integration
- account/position synchronization

所有しないもの:

- IC calculation
- research score bucket analysis
- historical Signal rewrite
- experimental model notebook logic

## Infrastructure placement

Providerごとにadapterを分ける。

例:

```text
infrastructure/
├── gmo_coin/
├── yfinance/
├── nhk/
├── llm/
└── persistence/
```

provider名をdomain module名にしない。

悪い例:

```text
domain/gmo_position.py
domain/openai_news_score.py
```

## Nested AGENTS.md

実repoでは以下を推奨する。

### `apps/fx_research/AGENTS.md`

Research固有のimmutable signal、forward evaluation、future leakage防止を記載する。

### `apps/swap_bot/AGENTS.md`

Live固有のRisk bypass禁止、Broker call境界、idempotencyを記載する。

ルート`AGENTS.md`へ全ルールを詰め込まない。
