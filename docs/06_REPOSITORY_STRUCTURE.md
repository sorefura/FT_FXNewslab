# Repository Structure

## ExecPlan 0006 target modules

Current `swap_bot` now has a `strategy/` package for the Milestone 2-A immutable
config, operational Swap evidence, production entry/exit contracts, and Ports. It
contains no concrete Strategy or infrastructure. The following remains the target
responsibility map:

```text
swap_bot/
  strategy/       M2-A contracts; NewsFilteredCarryStrategy remains pending
  signals/        operational Signal source/checkpoint adapter
  swap/           versioned operational Swap evidence/adapters
  paper/
    domain/       order lifecycle, fill, ledger, account, PnL, reconciliation
    application/  one-shot cycle, recovery, burn-in
    infrastructure/ SQLite store and public market/swap adapters
  operations/     scheduler, overlap lock, health and observability
  live/           existing adoption/Portfolio/Risk/Execution boundaries
```

Milestone 2-D adds the ordinary-close Strategy/Portfolio/Risk/persistence path across
five units, inside existing `swap_bot` module locations:

```text
apps/swap_bot/src/swap_bot/strategy/ordinary_close.py
    B1 typed Signal/Adoption terminal resolution, Position close capacity evidence,
    work/result envelopes, and the deterministic OrdinaryPositionExitEvaluator;
    B2 close-specific OrdinaryClosePortfolioDecision/OrdinaryCloseRiskDecision/
    ApprovedCloseIntent contracts and evaluate_ordinary_close_portfolio_and_risk()

apps/swap_bot/src/swap_bot/migrations/0005_ordinary_close_path.sql
    B3 exit-evaluation persistence tables (capacity, resolution, work item,
    operational evaluation, Candidate); B4 appends Portfolio decision, Risk
    decision, and ApprovedCloseIntent reservation tables

apps/swap_bot/src/swap_bot/ordinary_close_store.py
    B3 SQLiteOrdinaryCloseStore.evaluate_and_persist(); B4
    evaluate_and_persist_reservation() atomic capacity reservation

apps/swap_bot/src/swap_bot/ordinary_close_application.py
    B5 OrdinaryCloseApplicationService composing B3 evaluation persistence and B4
    reservation persistence into one single-Position KEEP/CLOSE flow
```

Milestone 3 froze and implemented the Paper domain/persistence/application path as a
flat `apps/swap_bot/src/swap_bot/paper/` package, superseding the speculative
`paper/domain/`/`paper/application/`/`paper/infrastructure/` sketch above:

```text
apps/swap_bot/src/swap_bot/paper/contracts.py
    B1 pure immutable domain: PaperIntentKind, PaperOrderIntentLineage, market
    observation/fill-policy/order/plan/Step/attempt/selection/Fill contracts

apps/swap_bot/src/swap_bot/paper/fill_engine.py
    B2 pure deterministic fill engine: eligibility, selection ordering,
    adverse-slippage fill price, partial-fill quantity, PENDING/terminal branch

apps/swap_bot/src/swap_bot/paper/ledger.py
    B3 pure accounting domain: position/account snapshots, the seven named
    formulas, swap accrual/correction, and the four reconciliation rebuilds

apps/swap_bot/src/swap_bot/migrations/0006_paper_execution_ledger.sql
    B4 additive Live migration: the 24 live_paper_* tables

apps/swap_bot/src/swap_bot/paper/store.py
    B4 SQLitePaperStore implementing the eight frozen transactions (T0-T7); the
    only Paper module importing sqlite3/live_migrations

apps/swap_bot/src/swap_bot/paper/application.py
    B5 Clock Protocol, UTCClock adapter, and PaperApplicationService composing
    B1-B4 into exactly three one-intent entry points (entry, ordinary-close,
    emergency-liquidation)
```

Milestone 2-B1 added the package-neutral identity and Pair materialization contract
modules without changing that Live module map:

```text
packages/fx_core/src/fx_core/identity.py
    package-neutral canonical JSON and SHA-256 identity

packages/fx_signal_store/src/fx_signal_store/pair_materialization.py
    Pair/as-of Specification and Request, exact Signal/Observation snapshots,
    BASE/QUOTE candidate inventory, full-inventory terminal resolver,
    deterministic Pair Signal ID, exact shared-transformer output verifier,
    and relational PairSignalDerivation
```

`swap_bot.adoption` keeps compatibility wrappers for its existing public digest API.
`fx_signal_store` imports neither `swap_bot` nor `fx_research`.

Milestones 2-B2 through 2-B4 add the shared Store persistence seams:

```text
packages/fx_signal_store/src/fx_signal_store/persistence.py
    Store entry/origin, Claim, Selection and Completion results, lineage, and errors

packages/fx_signal_store/src/fx_signal_store/store.py
    connection-scoped Signal/lineage helpers, atomic Signal/Store-entry append,
    exact Specification/Request/Claim persistence, and Claim-authorized Selection
    evidence capture plus exact Pair artifact Completion

packages/fx_signal_store/src/fx_signal_store/migrations/
    0001_signal_lineage.sql
    0002_pair_materialization_persistence.sql
    0003_pair_signal_selection_evidence.sql
    0004_pair_signal_artifact_persistence.sql

packages/fx_signal_store/src/fx_signal_store/materializer.py
    three-operation Store Protocol, four operational outcomes, validated frozen
    aggregate result, and Claim -> Selection -> Completion composition
```

Moving `SignalLineage` to the persistence seam prevents a Store-to-contract circular
import while retaining its existing public export. The 0002 migration contains Store
sequence, Specification, Request, and Claim tables. The 0003 migration adds only
immutable Selection Snapshot, complete candidate inventory, and candidate
Observation lineage tables. The 0004 migration adds Pair derivation scalar/ordered
Observation evidence and the terminal Completion root while reusing existing Signal,
Feature-lineage, and Store-entry tables for Pair artifacts. M2-B5 adds operational
materializer composition in the shared package without importing SQLite or either
application. No Live application table is added.

The actual migration remains incremental; files are moved only when the boundary is
implemented. Paper infrastructure may depend on Live-owned approved-intent contracts
but cannot import or construct the real Broker Private transport. It cannot import
`fx_research`; public Paper market data is exposed through a Live-owned Port.

The shared Signal Store migrations are now `0001` through `0004`. The independently
numbered Live migrations remain `0001` and `0002`; M2-A through M2-B4 add none there.
M2-B5 also adds no migration or persistence table.
Milestone 2-C added `0003_operational_swap_evidence.sql` and
`0004_production_entry_strategy.sql`; Milestone 2-D added
`0005_ordinary_close_path.sql`. Paper persistence begins at the next available
migration after that Strategy persistence and leaves the inline historical base
schema unchanged. No number is pre-reserved for Paper.

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
