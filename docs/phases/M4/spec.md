# M4 Frozen Specification — Operational Paper Cycle

Status: frozen design input for the Phase Goal workflow.

This snapshot defines Milestone 4 only. The living ExecPlan remains the place for
progress and decision-log updates; implementation must not edit this file after the
phase baseline is committed. `docs/phases/M3.toml` and every file under
`docs/phases/M3/` are closed and must not be modified.

Baseline: `961b4206e6f0c8853b8ba86039441ca7b6484066` (clean tree). Live migrations
`0001`-`0006` and the implemented `apps/swap_bot/src/swap_bot/paper/` package
(`contracts.py`, `fill_engine.py`, `ledger.py`, `store.py`, `application.py`) are
frozen public contracts M4 consumes unchanged.

## Objective

For one `CycleSlot`, freeze real operational Signal / Adoption / Swap / public market /
Paper position / Paper account input exactly once as an immutable
`CycleInputSnapshot`, and from that snapshot alone deterministically run:

```text
prevalidate authority and policy (LIVE rejected, zero durable work)
-> claim the Slot and freeze exactly one CycleInputSnapshot
-> account-wide emergency risk
     |-- emergency triggered  -> one ApprovedLiquidationIntent per frozen open
     |                           Position, and nothing else
     `-- no emergency         -> ordinary close over frozen open Positions
                                 -> entry over configured Pairs
-> dispatch every approved intent exactly once per attempt
     |-- SHADOW_NOT_SUBMITTED -> typed NOT_SUBMITTED semantic results only
     `-- PAPER                -> M3 PaperApplicationService with post-intent fill
                                 evidence
-> append semantic CycleWorkResults, then one CycleCompletion when every expected
   semantic work root is terminal
```

After crash, restart, or manual retry, source input is never reselected; the same
Slot converges to the same semantic state.

## Non-goals

- Recurring scheduler, daemon, long-lived process overlap lock, or any process-level
  ownership mechanism. M5 owns these.
- Health signals, metrics, alerts, burn-in report, or readiness report. M5 owns
  these.
- Automatic retry, sleep, or backoff of any kind, inside or around `run_once`.
- Real Broker adapter, `BrokerGateway` implementation, GMO Private POST, `LIVE`
  execution, canary rollout, or any `LiveArmPolicy` change.
- Strategy optimization, dynamic sizing, multi-strategy allocation, multi-account
  operation, or generic FX accounting.
- Rewriting accepted M2-A/M2-B/M2-C/M2-D/M3 semantic contracts. M4 adds only the
  additive operations named in this document.
- Swap accrual (M3 T5), swap correction (M3 T6), or any rollover composition. M4
  never calls those two M3 store methods.
- Position discovery outside the frozen snapshot, latest-row selection, or any
  implicit "current" record lookup.
- New Signal Store migration, new Signal semantics, or any change to
  `PairSignalMaterializationSpecification`, `PairSignalMaterializationRequest`, or the
  selection/derivation contracts.

## Cross-unit invariants

- `ExecutionAuthorityMode.LIVE` is rejected during prevalidation, before any `Clock`
  read, Signal Store call, Live database connection, market adapter call, or
  `PaperApplicationService` construction, and leaves zero durable rows anywhere.
- One semantic `CycleSlot` has exactly one `CycleInputSnapshot`. A retry reads it and
  performs zero source selection. A conflicting second snapshot fails closed
  atomically without partial rows.
- The `cycle` package never imports, constructs, or references `BrokerGateway`,
  `ExecutionService`, `GmoPrivatePostTransport`, `LiveArmPolicy`, any other real
  Broker transport, or any `fx_research` module.
- Only `cycle/store.py` may execute `BEGIN`, `COMMIT`, `ROLLBACK`, `INSERT`,
  `UPDATE`, or `DELETE`, and only `cycle/store.py` may import `live_migrations`.
  `cycle/inputs.py` may import `sqlite3` but performs read-only `SELECT` statements
  on a connection its caller supplies. No other `cycle` module imports `sqlite3`.
- Every M4 semantic identity is content-addressed with the existing canonical
  SHA-256 helper `swap_bot.adoption.digest`. Python `hash()` is forbidden. First-write
  audit timestamps are excluded from every semantic identity.
- All money, quantity, price, and margin values are `Decimal`. Binary float is
  forbidden in production code, persistence, and test expectations. SQLite `REAL` is
  never used for a semantic value.
- Every persistence boundary is append-or-compare. `INSERT OR IGNORE` alone is never
  proof of equality; the row is re-read and compared field by field. Every M4 table
  has UPDATE and DELETE rejection triggers.
- Persisted corruption, a missing parent, a contradiction between frozen evidence and
  persisted evidence, or a conflicting second write is an integrity failure. It is
  never a business outcome, never repaired, never retried automatically, and leaves no
  partial rows.
- Ordinary missing, stale, ambiguous, or absent operational business data is a typed
  Cycle result. It is never reclassified as corruption and never causes a
  substitution of a different source record into a frozen Slot.
- Every external boundary requires exact concrete runtime types, calls the base
  `__post_init__`/`validate_intrinsic_integrity` implementation directly, and rejects
  comparison-overriding `str` subclasses (`type(value) is str`) before any semantic
  equality test.
- Paper success, Paper PnL, or Cycle completion never creates Live authority and
  never changes Adoption state.

## Frozen time model

### The one semantic instant

- `CycleSlot` carries exact UTC-aware `scheduled_for` and `as_of`.
- `run_once` reads the injected `Clock` exactly once per call. That value is
  `attempt_instant`.
- When no `CycleInputSnapshot` exists for the Slot, `captured_at = attempt_instant`
  and `captured_at >= as_of` is required; a violation fails closed before any durable
  write.
- When a `CycleInputSnapshot` already exists, `captured_at` is the persisted
  first-write value. A later `Clock` value never replaces it.
- The first Signal Store batch claim persists `captured_at` before the Cycle snapshot
  exists, so a crash between those two writes still recovers the original semantic
  instant from the persisted Claims.

`captured_at` is the single semantic instant of the Slot. It is used for:

- both `PairSignalMaterializationClaim.captured_at` values and, when selected, both
  Pair Signal `materialized_at` values;
- `SignalAuthorization.authorized_at`;
- `ProductionEntryEvaluationInput.evaluated_at` and
  `ProductionPositionExitEvaluationInput.evaluated_at`;
- `OperationalSwapResolution.requested_at`;
- `PositionExitPositionEvidence.position_observed_at`;
- `PortfolioDecision.created_at`, `RiskDecision.created_at`, and the legacy
  `AccountSnapshot.observed_at` of the transient Risk view;
- `TradeCandidate.created_at` of the transient bridge, which equals the frozen
  `ProductionTradeCandidate.created_at`;
- `ApprovedExecutionIntent.created_at` and `ApprovedLiquidationIntent.created_at`
  created by this Slot.

`attempt_instant` is audit only. It is used for `CycleAttemptStart.started_at`,
`CycleAttemptTerminal.completed_at`, `CycleCompletion.completed_at`, and the
`resolved_at` argument of `SQLitePaperStore.reconcile_account`. It never enters
`CycleSlotId`, `input_snapshot_hash`, or any semantic work identity.

`PaperApplicationService` reads the same injected `Clock` once per one-intent call.
Those reads are M3's own evaluation instants and are neither `captured_at` nor
`attempt_instant`. M3's per-plan non-decreasing rule therefore governs them.

### `CycleSlotId`

```text
cycle_slot_id = "cycle-slot-" + digest({
    "slot_contract_version": "cycle-slot-v1",
    "scheduled_for":            scheduled_for.isoformat(),
    "as_of":                    as_of.isoformat(),
    "execution_authority_mode": execution_authority_mode.value,
    "strategy_id":              strategy_id,
    "strategy_version":         strategy_version,
    "strategy_config_identity": strategy_config_identity,
    "cycle_policy_version":     cycle_policy_version,
})
```

Those seven values are the complete identity payload, in that exact key order. Every
other value is excluded, in particular: every input record ID, the Signal Store
checkpoint, `captured_at`, `attempt_instant`, worker identity, attempt identity,
`input_snapshot_hash`, and the Paper account state.

Both `scheduled_for` and `as_of` must be exact `datetime`, UTC-aware, and satisfy
`scheduled_for <= as_of` is **not** required; they are independent and no ordering
between them is asserted, because a Slot may be scheduled before or after the data
cut-off it observes. Both are required arguments with no derivation and no default.

## Frozen `OperationalPaperCyclePolicy`

Immutable, content-addressed, no hidden default, no fixture promotion. Exact fields:

- `policy_contract_version` = `"operational-paper-cycle-policy-v1"`;
- `cycle_policy_version: str`, non-blank exact `str`;
- `paper_account_bootstrap: PaperAccountBootstrap` — the exact M3 contract;
- `pair_specifications: tuple[tuple[CurrencyPair, PairSignalMaterializationSpecification], ...]`
  — exactly one entry per configured Pair, in configured Pair order, each
  specification's `pair` equal to its key;
- `swap_source: str`, `swap_source_version: str`;
- `cycle_mark_source: str`, `cycle_mark_source_version: str`,
  `cycle_mark_maximum_age: timedelta` strictly positive;
- `entry_quantities: tuple[tuple[CurrencyPair, Decimal], ...]` — exactly one entry
  per configured Pair, in configured Pair order, each quantity positive and finite,
  in exact `BASE_UNITS`;
- `portfolio_exposure_limits: tuple[tuple[Currency, Decimal], ...]` — non-empty,
  unique keys sorted by `currency.code`, each limit positive and finite;
- `risk_policy: RiskPolicy` — the existing `swap_bot.risk.RiskPolicy`;
- `ordinary_close_allocation_policy: OrdinaryCloseAllocationPolicy`;
- `ordinary_close_risk_policy: OrdinaryCloseRiskPolicy`;
- `paper_fill_policy: PaperFillPolicy` — the exact M3 contract;
- `exit_input_policy_version: str`;
- `cycle_selection_policy_version: str`;
- `cycle_reconciliation_policy_version: str`;
- `pending_exposure_policy_version: str`;
- `policy_content_id` = `"cycle-policy-" + digest(<every field above except this
  one, in that order>)`.

`cycle_policy_version` is the primary key of `live_cycle_policies` and the row stores
the canonical JSON of the full identity payload. Presenting the same
`cycle_policy_version` with different content is corruption: append-or-compare fails
closed and no Slot is claimed.

The policy is validated against the supplied `NewsFilteredCarryStrategyConfig` before
any durable work: `pair_specifications` keys and `entry_quantities` keys must both
equal `config.eligible_pairs` exactly, in order; every specification's
`output_signal_type` must equal `config.expected_pair_signal_type`; every
specification's `output_transformation_version` must equal
`config.pair_transformation_version`. A mismatch fails closed.

M4 v1 supports exactly one Paper account: `policy.paper_account_bootstrap.paper_account_id`.

## Frozen two market evidence roles

M4 keeps the two roles apart by giving them different contract types and different
Ports. There is no shared supertype, no conversion function, and no adapter that
produces both.

### `CycleMarkObservation` (M4-owned, B1)

Immutable, content-addressed. Exact fields:

- `cycle_mark_observation_id` = `"cycle-mark-" + digest(identity payload)`;
- `observation_contract_version` = `"cycle-mark-observation-v1"`;
- `pair: CurrencyPair`;
- `bid: Decimal`, `ask: Decimal`, both positive and finite, `bid <= ask`;
- `provider_observed_at: datetime` UTC;
- `received_at: datetime` UTC, `provider_observed_at <= received_at`;
- `source: str`, `source_version: str`, both non-blank exact `str`.

The identity payload is every field above except the ID, in that order.

Role: it is selected and frozen as Cycle input and is used only for

- open Paper position marking in the Portfolio exposure input,
- the pending-exposure reference price, and
- the entry reference price.

It is never accepted by `PaperApplicationService`, never persisted into
`live_paper_market_observations`, and never used as fill evidence. It is a distinct
type from `PaperMarketObservation`, so passing one where the other is required raises
at M3's `type(o) is PaperMarketObservation` check.

`CycleMarkOutcome` is a `StrEnum` with exactly `OBSERVED`, `MISSING`, and `STALE`.

Frozen mark resolution for one configured Pair, evaluated at freeze time:

1. the `CycleMarkSource` returns `None` -> `MISSING`;
2. the returned value is not exactly `CycleMarkObservation`, or its `pair` differs
   from the requested Pair, or its `source`/`source_version` differ from the policy,
   or `received_at > as_of` -> integrity failure (the adapter violated its contract);
3. `as_of - provider_observed_at > policy.cycle_mark_maximum_age` -> `STALE`
   (equality remains `OBSERVED`);
4. otherwise `OBSERVED`.

`MISSING` and `STALE` are typed business results. Only `OBSERVED` stores a mark in
the snapshot.

Reference price rules, all exact and frozen:

```text
entry reference price     : BUY  -> mark.ask       SELL  -> mark.bid
pending intent reference  : BUY  -> mark.ask       SELL  -> mark.bid
open position current mark: LONG -> mark.bid       SHORT -> mark.ask
```

### `PaperFillObservation` (M3-owned, unchanged)

The fill evidence contract is exactly M3's `PaperMarketObservation`. M4 adds no
subtype and no wrapper. It is:

- not part of `CycleInputSnapshot`;
- acquired only after the corresponding approved intent exists, through the separate
  `PaperFillObservationSource` Port;
- governed entirely by M3's frozen eligibility, window, freshness, and
  local-availability rules.

A retry may supply new eligible fill observations to an unresolved M3 Step; M3 alone
decides whether they are eligible.

## Frozen `CycleInputSnapshot`

One snapshot per Slot, immutable, first-write. Its exact lineage is:

**Root**

- `cycle_input_snapshot_id` = `"cycle-input-snapshot-" + input_snapshot_hash`;
- `snapshot_contract_version` = `"cycle-input-snapshot-v1"`;
- `cycle_slot_id`, `scheduled_for`, `as_of`;
- `execution_authority_mode`, `strategy_id`, `strategy_version`,
  `strategy_config_identity`, `cycle_policy_version`, `policy_content_id`;
- `signal_store_checkpoint_sequence: int` — one shared checkpoint for both Pairs;
- `paper_account_id`;
- `paper_account_state_kind` in `{BOOTSTRAP_ONLY, SNAPSHOT}`;
- `paper_account_snapshot_id: str | None` — exactly `None` for `BOOTSTRAP_ONLY`;
- `paper_reconciliation_result_id: str | None` — exactly `None` for `BOOTSTRAP_ONLY`;
- `highest_application_seq: int`, `highest_ledger_entry_seq: int`,
  `highest_order_event_seq: int` — all `0` for `BOOTSTRAP_ONLY`;
- `account_equity: Decimal`, `account_used_margin: Decimal`;
- `exit_input_policy_version`, `cycle_selection_policy_version`,
  `cycle_reconciliation_policy_version`, `pending_exposure_policy_version`,
  `risk_policy_version`, `ordinary_close_allocation_policy_version`,
  `ordinary_close_risk_policy_version`, `paper_fill_policy_id`,
  `adoption_policy_version`;
- `input_snapshot_hash`;
- first-write audit `captured_at`.

**Per-Pair lineage, ordered by configured Pair ordinal**

- `pair_ordinal: int`, `pair`;
- `materialization_specification_id`, `materialization_request_id`;
- `claim_checkpoint_sequence` (equal to the shared checkpoint),
  `claim_captured_at` (equal to `captured_at`);
- `selection_snapshot_id`, `completion_id`, `materializer_outcome`;
- `selected_signal_id: str | None`, `pair_signal_content_hash: str | None` — both
  present exactly when the materializer outcome is `MATERIALIZED` or
  `REUSED_IDENTICAL`;
- `authorization_id: str | None`, `adoption_decision_id: str | None`,
  `adoption_evidence_snapshot_id: str | None` — all present exactly when the Pair was
  authorized;
- `signal_resolution_id`, `signal_resolution_outcome` in
  `{AUTHORIZED, NO_SELECTION, AMBIGUOUS, ADOPTION_INACTIVE}`,
  `signal_resolution_reason_code`;
- `swap_resolution_id`, `swap_source`, `swap_source_version`, `swap_outcome`,
  `swap_reason_code`, `swap_evidence_id: str | None`;
- `cycle_mark_outcome`, and for `OBSERVED` the full mark content
  (`cycle_mark_observation_id`, `bid`, `ask`, `provider_observed_at`, `received_at`,
  `source`, `source_version`).

**Ordered frozen open Paper positions** (ordered by configured Pair ordinal, then
`paper_position_id` ascending)

- `position_ordinal`, `paper_position_id`, `entry_paper_order_id`, `pair`,
  `position_side` in `{LONG, SHORT}`, `open_quantity: Decimal` (strictly positive),
  `entry_intent_created_at`, `position_highest_application_seq`.

**Ordered frozen nonterminal Paper orders** (ordered by `paper_order_id` ascending)

- `order_ordinal`, `paper_order_id`, `intent_kind`, `pair`, `side`,
  `original_quantity: Decimal`, `remaining_quantity: Decimal` (strictly positive),
  `projected_state` in `{ACCEPTED, OPEN, PARTIALLY_FILLED}`.

**`input_snapshot_hash`**

```text
input_snapshot_hash = digest({
    "snapshot_contract_version": ...,
    "cycle_slot_id": ..., "scheduled_for": ..., "as_of": ...,
    "execution_authority_mode": ..., "strategy_id": ..., "strategy_version": ...,
    "strategy_config_identity": ..., "cycle_policy_version": ...,
    "policy_content_id": ...,
    "signal_store_checkpoint_sequence": ...,
    "paper_account_id": ..., "paper_account_state_kind": ...,
    "paper_account_snapshot_id": ..., "paper_reconciliation_result_id": ...,
    "highest_application_seq": ..., "highest_ledger_entry_seq": ...,
    "highest_order_event_seq": ...,
    "account_equity": str(...), "account_used_margin": str(...),
    "policy_versions": {<the nine version fields above, in the listed order>},
    "pairs": [<each Pair lineage record above, in pair_ordinal order>],
    "positions": [<each position record above, in position_ordinal order>],
    "open_orders": [<each order record above, in order_ordinal order>],
})
```

`captured_at` is audit and is deliberately excluded from `input_snapshot_hash`, so a
retry that recomputes the same semantic content produces the same hash regardless of
the current `Clock`. `Decimal` is serialised with `str(...)`, `datetime` with
`.isoformat()`, `CurrencyPair` with `.symbol`, and enums with `.value`. Ordered
collections retain explicit ordinals; there is no set-like collection in this payload.

## Frozen Signal source consistency

- The configured Pairs are exactly `USD_JPY` and `MXN_JPY`, in configured order.
  M4 derives no Pair set of its own.
- Both `PairSignalMaterializationRequest`s are derived deterministically from the Slot:
  `PairSignalMaterializationRequest.create(contract_version=<the specification's
  request contract version already used by M2-C>, pair=<configured Pair>,
  as_of=slot.as_of, specification=policy.pair_specifications[pair])`. A Slot therefore
  has exactly one Request pair, and a retry recomputes the identical `request_id`s.
- B2 adds one additive Signal Store operation that first-claims both Requests in one
  writer transaction:

  ```python
  def claim_pair_signal_materializations(
      self,
      requests: tuple[PairSignalMaterializationRequest, ...],
      *,
      captured_at: datetime,
  ) -> tuple[PairSignalMaterializationClaim, ...]: ...
  ```

  Frozen SQL contract: exactly one `BEGIN IMMEDIATE`; `MAX(store_sequence)` and the
  full catalog integrity validation are evaluated exactly once for the whole batch;
  the same `current_checkpoint` and the same `captured_at` are used for every Request;
  Specification and Request are append-or-compare persisted per Request in the given
  order; an existing Claim is returned unchanged with its original checkpoint and
  `captured_at`; every returned Claim is revalidated against the one current Store
  boundary; any failure rolls the whole batch back. Requests must be non-empty and
  have unique `request_id`s; a duplicate `request_id` in the argument is a caller
  error and fails closed before the transaction opens.

  Both the existing single-Request `claim_pair_signal_materialization` and the new
  batch method delegate to one connection-scoped private helper, so exactly one claim
  implementation exists. The single-Request method keeps its exact signature,
  behaviour, transaction boundary, and error types.

- B2 also adds one additive read-only Signal Store operation:

  ```python
  def get_pair_signal_materialization_result(
      self, request: PairSignalMaterializationRequest
  ) -> PairSignalMaterializerResult | None: ...
  ```

  It opens no writer transaction and writes nothing. It returns `None` when no Claim
  exists for the Request. It hydrates the persisted Claim, Selection snapshot, and
  Completion through the existing private hydration helpers and assembles the exact
  `PairSignalMaterializerResult` with the same `_operational_outcome` mapping, using
  `PairSignalMaterializationCompletionDisposition.REUSED_IDENTICAL` for a persisted
  `SELECTED` Completion. A Claim without a persisted Selection or Completion is a
  `SignalStoreIntegrityError`.

- The snapshot records one shared checkpoint plus the full Request/Claim/Selection/
  Completion lineage per Pair.

## Frozen atomic Cycle snapshot freeze

The freeze is one Live-database `BEGIN IMMEDIATE` transaction owned by
`SQLiteOperationalCycleStore` (B4). Its exact ordered steps are:

1. append-or-compare the `live_cycle_policies` row for `cycle_policy_version`;
2. read the `live_cycle_input_snapshots` row for `cycle_slot_id`. When it exists,
   hydrate the complete snapshot, compare its recomputed `input_snapshot_hash` with
   the persisted value, commit, and return it. **No selection of any kind runs on
   this path**;
3. otherwise enforce the one-account incomplete-slot rule (below);
4. append-or-compare the `live_cycle_slots` row;
5. authenticate and select every Live-owned input on this connection, through
   `cycle/inputs.py` read-only selectors and the connection-scoped adoption
   authorization:
   - per Pair, in configured order: authenticate the supplied
     `PairSignalMaterializerResult` against the frozen Request, then authorize its
     reconstructed Pair Signal with `authorize_signal_on(connection, ...)` when the
     materializer outcome is `MATERIALIZED`/`REUSED_IDENTICAL`, producing the exact
     `AuthorizedSignal` and the typed `SignalAdoptionTerminalResolution`;
   - per Pair: append-or-compare the resolved `OperationalSwapEvidence` with
     `SQLiteOperationalSwapStore.append_or_compare_on(connection, evidence)` when the
     resolution outcome is `EVIDENCE`, and record the exact resolution lineage;
   - per Pair: record the resolved `CycleMarkObservation` or its typed outcome;
   - the Paper account state, open positions, and nonterminal orders (below);
   - every exact policy/version root listed in the snapshot;
6. build the snapshot, compute `input_snapshot_hash`, insert the root and the ordered
   Pair/position/open-order lineage rows;
7. re-read every inserted row, rebuild the snapshot from the persisted rows, and
   require it to equal the constructed value before commit.

Adoption authorization therefore happens on the same connection inside the same
writer transaction as the snapshot insert. Authorizing first and snapshotting later
is prohibited.

The Signal Store lives in a separate SQLite database, so its batch claim is
necessarily a separate transaction that runs before this one. That is the frozen
crash boundary "after Signal batch Claims but before Cycle snapshot": the persisted
Claims carry the first-write `captured_at` and checkpoint, so recovery reuses them
rather than reading the `Clock` again.

Later, backfilled, or corrected source evidence may affect only a later Slot. A
contradiction or corruption discovered while authenticating a frozen record fails
closed; it never substitutes a new source record into the frozen Slot.

### Frozen Paper state reconciliation before claim

Before opening the freeze transaction, the service determines the account's Paper
state class with a read-only check:

- **Fresh account** — zero rows in `live_paper_positions`, `live_paper_orders`, and
  `live_paper_ledger_entries` for that `paper_account_id`. No reconciliation is
  performed and no `live_paper_*` row is written by the freeze. If a
  `live_paper_account_bootstraps` row exists for the account it must equal
  `policy.paper_account_bootstrap` exactly, otherwise integrity failure. The frozen
  account state is `BOOTSTRAP_ONLY` with `account_equity =
  bootstrap.initial_cash`, `account_used_margin = Decimal(0)`, and all three
  boundaries `0`.
- **Established account** — otherwise. The `live_paper_account_bootstraps` row must
  exist and equal `policy.paper_account_bootstrap` exactly. The service calls M3's
  `SQLitePaperStore.reconcile_account(paper_account_id=..., resolved_at=attempt_instant)`
  and requires `outcome is PaperReconciliationOutcome.MATCHED`. A `MISMATCHED`
  outcome, or any exception from that call, is an integrity failure that writes no
  Cycle snapshot.

Inside the freeze transaction the established-account path re-reads that exact
`reconciliation_result_id` row and requires:

- its `outcome` to be `MATCHED`;
- its `highest_application_seq`, `highest_ledger_entry_seq`, and
  `highest_order_event_seq` to equal the account's current maxima computed on the same
  connection.

Any inequality means Paper state changed between the rebuild and the freeze, and is an
integrity failure. This is what makes "rebuild exactly before the snapshot commits"
provable: the reconciliation that passed is over exactly the state being frozen, and
it is M3's own full rebuild of all four record kinds, not a stored summary.

### Frozen Paper account state selection

For an established account, the frozen account snapshot is the unique
`live_paper_account_snapshots` row of that account whose `highest_application_seq` and
`highest_ledger_entry_seq` both equal the reconciliation result's corresponding
boundaries.

Uniqueness is a consequence of M3, not an assumption: every M3 transaction that writes
an account snapshot (T3a, T3b, T5-accrual, T6) advances `highest_application_seq` or
`highest_ledger_entry_seq`, so no two account snapshots of one account share that
pair. Zero matching rows when either boundary is non-zero, or more than one matching
row, is an integrity failure. This is an exact boundary-equality match, never
`ORDER BY ... DESC LIMIT 1`.

`account_equity` and `account_used_margin` are copied from that snapshot's `equity`
and `used_margin` columns.

### Frozen open Paper positions

For each `live_paper_positions` row of the account:

```text
open_quantity = sum(quantity of ENTRY applications)
              - sum(quantity of REDUCE_ONLY applications)
   over that position's live_paper_position_fill_applications rows with
   application_seq <= highest_application_seq
```

A position with `open_quantity > 0` is frozen; `open_quantity == 0` is excluded;
`open_quantity < 0` is an integrity failure. `entry_intent_created_at` is the
`intent_created_at` of the `live_paper_orders` row named by `entry_paper_order_id`.
`position_highest_application_seq` is the greatest `application_seq` of that position
at or below the boundary.

Every frozen open position's `pair` must be a member of `config.eligible_pairs`;
otherwise integrity failure.

### Frozen nonterminal Paper orders

For each `live_paper_orders` row of the account, project its state with M3's
`project_paper_order_state` over its `live_paper_order_events` rows with
`order_event_seq <= highest_order_event_seq`. An order with no event at or below the
boundary did not yet exist and is excluded before any projection.

An order whose projected state is `ACCEPTED`, `OPEN`, or `PARTIALLY_FILLED` is
nonterminal and is frozen with

```text
remaining_quantity = original_quantity
                   - sum(fill_quantity of every persisted PaperFill of that order's
                     plan's Steps)
```

derived from the persisted plan/Step/Fill lineage only. A non-positive
`remaining_quantity` on a nonterminal order is an integrity failure. Every frozen
nonterminal order's `pair` must be a member of `config.eligible_pairs`.

Frozen nonterminal orders contribute to pending exposure only. They are never
expected semantic work roots of this Slot.

## Frozen entry Portfolio/Risk bridge

`ProductionTradeCandidate` remains authoritative and unchanged. `PairScore` is never
clamped, converted, or passed through `Probability`.

For the existing `PortfolioService` and `RiskService` only, B3 builds an exact
transient legacy view:

```python
TradeCandidate(
    candidate_id=CandidateId(production_candidate.candidate_id),
    strategy_id=production_candidate.strategy_id,
    strategy_version=production_candidate.strategy_version,
    pair=production_candidate.pair,
    side=production_candidate.side,
    score=production_candidate.confidence,
    signal_ids=(production_candidate.signal_id,),
    created_at=production_candidate.created_at,
)
```

`score` is exactly the production Candidate's `confidence`, which is already a
`Probability`; `pair_score` is never read by the bridge. The bridge value is
constructed in memory, used for the two service calls, and discarded. It is never
written to `live_candidates` or to any other legacy entry table. Persisted M4 entry
decision evidence references the exact `live_production_trade_candidates`
`candidate_id`.

Entry requested quantity comes only from `policy.entry_quantities` for that Pair.
Strategy never decides quantity. Entry reference price comes only from the frozen
`CycleMarkObservation` for that Pair under the BUY-ask / SELL-bid rule.

### Frozen Portfolio exposure input

`positions` supplied to `PortfolioService.evaluate` is built from the frozen open
positions, in the frozen ordinal order:

```python
PositionSnapshot(
    position_id=PositionId(frozen.paper_position_id),
    pair=frozen.pair,
    side=Side.BUY if frozen.position_side is LONG else Side.SELL,
    quantity=frozen.open_quantity,
    current_price=mark.bid if frozen.position_side is LONG else mark.ask,
    observed_at=captured_at,
)
```

`pending_intents` supplied to `PortfolioService.evaluate` is, in this exact order:

1. one `PendingIntent` per frozen nonterminal Paper order, in frozen ordinal order,
   with `quantity = remaining_quantity` and
   `reference_price = mark.ask` for `Side.BUY` / `mark.bid` for `Side.SELL`;
2. one `PendingIntent` per intent approved earlier in this same deterministic Cycle —
   every ordinary-close `ApprovedCloseIntent` and every entry
   `ApprovedExecutionIntent` already created by this Slot, in creation order — with
   `quantity` the approved quantity and the same side-specific reference price.

The second configured Pair therefore sees the allocation already committed by the
first Pair and by every new ordinary close.

Item 2 is rebuilt on every attempt from persisted M4 evidence (the Slot's persisted
`ORDINARY_CLOSE_POSITION` and `ENTRY_PAIR` work results and their persisted decision
chains), never from in-memory state of the current process, so a resumed attempt sees
the identical pending set.

## Frozen account Risk view and emergency

B3 derives an explicit transient `CycleAccountRiskView` from the frozen account
evidence:

- `equity = snapshot.account_equity`;
- `used_margin = snapshot.account_used_margin`;
- when `used_margin == 0`: `margin_ratio = None` and there is no margin emergency;
- otherwise `margin_ratio = equity / used_margin` computed inside
  `decimal.localcontext(PAPER_QUOTIENT_ARITHMETIC_V1)` — the exact frozen M3
  deterministic quotient context;
- `emergency = margin_ratio is not None and margin_ratio < policy.risk_policy.minimum_margin_ratio`.

Strictly below triggers; equality does not. M4 adds no new threshold and no second
margin rule.

The legacy `AccountSnapshot` passed to `RiskService.evaluate` is:

```python
AccountSnapshot(
    margin_ratio=(policy.risk_policy.minimum_margin_ratio
                  if view.margin_ratio is None else view.margin_ratio),
    observed_at=captured_at,
)
```

The `None` substitution is explicit and auditable: `RiskService` compares
`margin_ratio < minimum_margin_ratio`, equality does not reject, and the persisted M4
entry Risk evidence records `account_margin_ratio_kind` in
`{NO_USED_MARGIN, RATIO}` so the substitution is never mistaken for an observed ratio.
`observed_at = captured_at` is honest: the account state was selected at that instant,
so the `maximum_account_age` check measures zero age.

When `emergency` is true:

- exactly one `live_cycle_emergency_risk_decisions` row is written for the Slot,
  carrying `risk_policy_version`, `equity`, `used_margin`, `margin_ratio`, and
  `minimum_margin_ratio`. Its identity is
  `"cycle-emergency-risk-" + digest({"cycle_slot_id", "cycle_policy_version",
  "risk_policy_version", "equity", "used_margin", "margin_ratio",
  "minimum_margin_ratio"})`;
- exactly one `ApprovedLiquidationIntent` is created per frozen open Position, in the
  frozen position ordinal order:

  ```python
  payload = {"role": <role>, "cycle_slot_id": ..., "cycle_policy_version": ...,
             "emergency_risk_decision_id": ..., "paper_position_id": ...}
  ApprovedLiquidationIntent(
      intent_id=ExecutionIntentId("cycle-emergency-intent-" + digest(payload with
                                   role="EXECUTION_INTENT")),
      risk_decision_id=RiskDecisionId(emergency_risk_decision_id),
      position_id=PositionId(frozen.paper_position_id),
      pair=frozen.pair,
      quantity=frozen.open_quantity,
      idempotency_key="cycle-emergency-" + digest(payload with role="IDEMPOTENCY"),
      created_at=captured_at,
  )
  ```

  `existing_position_side` supplied to M3 is `Side.BUY` for a `LONG` frozen position
  and `Side.SELL` for a `SHORT` one;
- ordinary close is not evaluated and no entry Candidate is evaluated;
- under `PAPER`, each intent is routed to
  `PaperApplicationService.submit_emergency_liquidation_intent`;
- under `SHADOW_NOT_SUBMITTED`, only typed `NOT_SUBMITTED` semantic results are
  persisted.

`RiskService.create_liquidation_intents` is deliberately not used: it requires a
legacy `RiskDecision` whose parent is a legacy `PortfolioDecisionId`, and M4 must not
forge a legacy Portfolio parent. The M4 emergency decision root is the persisted
authority for `ApprovedLiquidationIntent.risk_decision_id`.

## Frozen deterministic cycle order

When no emergency triggers:

1. **ordinary close** over the frozen open positions, ordered by configured Pair
   ordinal, then `paper_position_id` ascending;
2. **entry** over `config.eligible_pairs` in configured order.

Business outcomes — KEEP, entry skip, Portfolio REJECT, Risk REJECT, `MISSING`/`STALE`
mark, `NO_SELECTION`/`AMBIGUOUS`/`ADOPTION_INACTIVE` Signal, non-`EVIDENCE` Swap — are
typed terminal `CycleWorkResult`s and the Cycle continues to later independent work.
Integrity or corruption failures stop the attempt fail-closed.

### Frozen emergency/ordinary-close mutual exclusion

One Position can never be acted on by both paths:

- within a Slot, emergency is evaluated first and, when it triggers, the ordinary
  close and entry stages are not run at all;
- `live_cycle_work_results` carries a `BEFORE INSERT` trigger that rejects an
  `EMERGENCY_POSITION` row when the same Slot already holds an
  `ORDINARY_CLOSE_POSITION` or `ENTRY_PAIR` row, and rejects an
  `ORDINARY_CLOSE_POSITION` or `ENTRY_PAIR` row when the same Slot already holds an
  `EMERGENCY_POSITION` row;
- across Slots, the one-account incomplete-slot rule forbids claiming a different
  Slot while this Slot is incomplete, and a Slot is incomplete while any of its
  approved intents has a nonterminal Paper order. A mid-flight ordinary close
  therefore blocks the Slot that would emergency-liquidate the same Position.

## Frozen ordinary close

Each frozen open Position produces exactly one `OrdinaryPositionExitWorkItem`, built
only from frozen Cycle evidence and the supplied `NewsFilteredCarryStrategyConfig`:

- `evaluation_input = ProductionPositionExitEvaluationInput(`
  `strategy_id=config.strategy_id, strategy_version=config.strategy_version,`
  `approved_strategy_config_identity=config.strategy_config_identity,`
  `position_id=PositionId(frozen.paper_position_id), pair=frozen.pair,`
  `existing_position_side=Side.BUY if LONG else Side.SELL,`
  `evidence_context=<below>,`
  `authorized_pair_signal=<the frozen AuthorizedSignal of that Pair or None>,`
  `swap_evidence=<the frozen OperationalSwapEvidence of that Pair or None>,`
  `evaluated_at=captured_at)`;
- `evidence_context = PositionExitEvidenceContext(`
  `position=PositionExitPositionEvidence(position_id=..., `
  `position_evidence_id=frozen.paper_position_id, pair=..., `
  `existing_position_side=..., position_opened_at=frozen.entry_intent_created_at,`
  `position_observed_at=captured_at),`
  `signal_selection_checkpoint_id=str(signal_store_checkpoint_sequence),`
  `swap_selection_checkpoint_id=<that Pair's swap_resolution_id>,`
  `expected_signal_specification_identity=<that Pair's specification_id>,`
  `prior_adoption_decision_id=<that Pair's adoption_decision_id, or the frozen`
  ` typed no-authorization marker recorded in the snapshot>,`
  `adoption_state_evidence_id=<that Pair's adoption_evidence_snapshot_id, or the`
  ` same marker>, exit_input_policy_version=policy.exit_input_policy_version)`;
- `capacity = PositionCloseCapacityEvidence.create(capacity_contract_version=<the`
  ` version already accepted by M2-D>, position_id=..., `
  `position_evidence_id=frozen.paper_position_id, pair=..., `
  `existing_position_side=..., position_observed_at=captured_at,`
  `open_quantity=frozen.open_quantity, quantity_unit="BASE_UNITS",`
  `source=<policy.cycle_selection_policy_version>, `
  `checkpoint_id=<snapshot.cycle_input_snapshot_id>)`;
- `signal_resolution` is the exact frozen `SignalAdoptionTerminalResolution` of that
  Pair, reconstructed from the snapshot and required to reproduce the frozen
  `signal_resolution_id`;
- `swap_resolution` is the exact frozen `OperationalSwapResolution` of that Pair,
  reconstructed with `OperationalSwapResolution.create(...)` and required to reproduce
  the frozen `swap_resolution_id`;
- `allocation_policy`, `risk_policy`, and `authority` come from the policy and the
  Slot.

The work item is then run through the unchanged
`OrdinaryCloseApplicationService.run(work_item, config=config)`, which reuses the
frozen M2-D evaluation persistence and reservation semantics. M4 adds no ordinary-close
evaluation, Portfolio, Risk, or reservation logic and writes to no M2-D table.

A later contradiction between frozen evidence and persisted M2-D/M2-C state fails
closed. It never substitutes another Signal, Authorization, Swap, or Position input.
`ApprovedCloseIntent` keeps its M2-D reservation semantics exactly; M3 alone consumes
or releases the reservation.

The `ORDINARY_CLOSE_POSITION` work result records the typed outcome: `KEEP` with its
keep reason, `PORTFOLIO_REJECT`, `RISK_REJECT`, or `CLOSE_APPROVED` with the
`ApprovedCloseIntent.idempotency_key` and quantity.

## Frozen entry

The entry stage never re-selects or re-authorizes a Pair input. It uses the exact
frozen evidence:

- `PairSignalMaterializerResult` — on the attempt that froze the snapshot it is the
  value returned by `OperationalPairSignalMaterializer.materialize(request,
  claim_captured_at=captured_at, materialized_at_if_selected=captured_at)`; on every
  later attempt it is the value returned by the read-only
  `get_pair_signal_materialization_result(request)`, whose Claim `captured_at`,
  checkpoint, `selection_snapshot_id`, and `completion_id` must equal the frozen
  values;
- `AuthorizedSignal` — reconstructed from the frozen `authorization_id` through
  `SQLiteAdoptionStore.get_authority_on` plus
  `reconstruct_materialized_pair_signal(result)`, and required to equal the frozen
  lineage;
- `OperationalSwapResolution` — reconstructed as above.

Per configured Pair, in configured order, the frozen precedence is:

1. mark outcome of **any** configured Pair is not `OBSERVED` ->
   `NOT_EVALUATED_MARK_UNAVAILABLE`. (Exposure can exist only in configured Pairs, so
   one condition covers both "this Pair has no reference price" and "some exposed Pair
   cannot be marked".)
2. materializer outcome `NO_SELECTION` -> `NOT_EVALUATED_PAIR_NO_SELECTION`;
   `AMBIGUOUS` -> `NOT_EVALUATED_PAIR_AMBIGUOUS`;
3. no frozen authorization -> `NOT_EVALUATED_ADOPTION_INACTIVE`;
4. Swap resolution outcome `MALFORMED` -> `NOT_EVALUATED_SWAP_MALFORMED`; any other
   non-`EVIDENCE` outcome -> `NOT_EVALUATED_SWAP_MISSING`;
5. otherwise call

   ```python
   SQLiteProductionEntryStore.evaluate_and_persist(
       config=config,
       materializer_result=<frozen result>,
       swap_resolution=<frozen resolution>,
       evaluation_input=ProductionEntryEvaluationInput(
           authorized_pair_signal=<frozen AuthorizedSignal>,
           approved_strategy_config_identity=config.strategy_config_identity,
           evaluated_pair=<Pair>,
           swap_evidence=<frozen evidence>,
           evaluated_at=captured_at,
       ),
   )
   ```

   `ProductionEntryApplicationService` is deliberately not used: its `run` calls the
   materializer and the Adoption gate itself and would therefore re-materialize and
   re-authorize after the snapshot freeze. The M2-C B4 store re-authenticates its
   persisted Adoption/Swap/config parents inside its own transaction and fails on
   contradiction; it selects no replacement.

   A persisted `SKIP` evaluation is the terminal `ENTRY_PAIR` result with its exact
   skip reason. A persisted `CANDIDATE` evaluation continues to Portfolio and Risk.

For a Candidate, the deterministic identities are, with

```text
base = {"cycle_slot_id": ..., "cycle_policy_version": ...,
        "strategy_config_identity": ...,
        "production_candidate_id": <live_production_trade_candidates.candidate_id>}
```

```text
portfolio_decision_id = PortfolioDecisionId(
    "cycle-entry-portfolio-" + digest(base | {"role": "PORTFOLIO"}))
risk_decision_id      = RiskDecisionId(
    "cycle-entry-risk-"      + digest(base | {"role": "RISK"}))
execution_intent_id   = ExecutionIntentId(
    "cycle-entry-intent-"    + digest(base | {"role": "EXECUTION_INTENT"}))
idempotency_key       =
    "cycle-entry-"           + digest(base | {"role": "IDEMPOTENCY"})
```

The stage then calls the unchanged `PortfolioService.evaluate(...)` and
`RiskService.evaluate(...)`, and on Risk `APPROVE` the unchanged
`RiskService.create_execution_intent(...)` with `created_at=captured_at`.

The complete Portfolio/Risk/`ApprovedExecutionIntent` chain is persisted
append-or-compare in M4 Cycle persistence. Nothing is written to `live_candidates`,
`live_portfolio_decisions`, `live_risk_decisions`, `live_execution_intents`, or any
other legacy REAL-score entry table.

The `ENTRY_PAIR` work result records the typed outcome:
`NOT_EVALUATED_<reason>`, `SKIPPED` with its exact skip reason, `PORTFOLIO_REJECT`,
`RISK_REJECT`, or `ENTRY_APPROVED` with the `execution_intent_id` and quantity.

## Frozen authority behaviour

**LIVE**

- rejected during prevalidation by `require_execplan_0006_authority`, as the first
  statement of `OperationalPaperCycleService.run_once` and again in the CLI before any
  object construction;
- before the `Clock` is read, before any Signal Store call, before any Live database
  connection, before any market adapter call, and before `PaperApplicationService` is
  constructed;
- zero durable work of any kind.

**SHADOW_NOT_SUBMITTED**

- the Cycle snapshot, Strategy evaluation, Portfolio decision, Risk decision, and
  approved-intent evidence may persist;
- `PaperApplicationService` is never constructed and never called;
- on a fresh Paper account the run writes exactly zero `live_paper_*` rows;
- on an established Paper account it writes exactly one
  `live_paper_reconciliation_results` row (M3's own MATCHED rebuild evidence, required
  by the frozen reconciliation rule) and zero rows in every other `live_paper_*`
  table.

**PAPER**

- only exact approved intents created by this Slot may enter
  `PaperApplicationService`, through its three existing entry points;
- no `BrokerGateway`, `ExecutionService`, or `GmoPrivatePostTransport` is imported,
  constructed, or called.

Paper success never creates Live authority.

## Frozen Paper dispatch

After the decision stages, every approved intent of this Slot is dispatched in
creation order: emergency intents in frozen position ordinal order, otherwise every
`ApprovedCloseIntent` in frozen position ordinal order followed by every
`ApprovedExecutionIntent` in configured Pair order.

For each approved intent:

1. if a terminal `PAPER_COMPLETION` work result already exists for that dispatch root,
   skip it entirely — no adapter call, no `PaperApplicationService` call;
2. under `SHADOW_NOT_SUBMITTED`, append the `INTENT_DISPATCH` and `PAPER_COMPLETION`
   work results with result code `SHADOW_NOT_SUBMITTED` and make no adapter or Paper
   call;
3. under `PAPER`, acquire post-intent fill evidence through
   `PaperFillObservationSource.observe(pair, not_before=intent.created_at)`. Every
   returned value must be exactly `PaperMarketObservation` with
   `received_at >= intent.created_at`; a violation is an integrity failure, because a
   pre-intent observation must never be able to fill;
4. call the matching `PaperApplicationService` entry point exactly once for this
   attempt, with `authority=PAPER`, `fill_policy=policy.paper_fill_policy`,
   `account_bootstrap=policy.paper_account_bootstrap`, and those observations;
5. append the `INTENT_DISPATCH` work result on the first successful call, recording the
   dispatch outcome;
6. append the `PAPER_COMPLETION` work result only when the returned
   `projected_order_state` is one of `FILLED`, `CANCELLED`, `EXPIRED`, `REJECTED`.

There is no loop, sleep, backoff, or automatic retry. A `PAPER_STEP_PENDING` result,
or a positive partial fill that permits another Step, leaves the Slot `INCOMPLETE`. A
later retry of the same Slot may obtain new post-intent fill observations and resume
the existing M3 Step or its next Step. It never reruns Strategy, Portfolio, or Risk,
and never creates a second intent for already-durable work, because every semantic
work root already holds its append-or-compare result.

## Frozen attempt model

Attempts are append-only and are never mutated:

```text
CycleAttemptStart -> zero or one CycleAttemptTerminal
```

- `CycleAttemptStart`: `cycle_attempt_start_id` =
  `"cycle-attempt-start-" + digest({cycle_slot_id, cycle_input_snapshot_id,
  worker_identity, started_at})`, plus `attempt_seq INTEGER PRIMARY KEY AUTOINCREMENT`
  as insert-order audit. It is appended in its own transaction immediately after the
  snapshot exists.
- `CycleAttemptTerminal`: `cycle_attempt_terminal_id`, `cycle_attempt_start_id`
  (UNIQUE, so at most one terminal per start), `outcome` in
  `{COMPLETED, INCOMPLETE, FAILED_INTEGRITY}`, `failure_classification: str | None`
  (exactly `None` unless the outcome is `FAILED_INTEGRITY`), `completed_at`.

A hard crash may leave a Start without a Terminal; that is a legal persisted state and
is never repaired. A retry appends another Start referencing the same Slot and the
same snapshot. An integrity failure after the Start exists appends one
`FAILED_INTEGRITY` terminal in its own transaction and then re-raises the original
exception.

Attempt identity, time, and worker identity never enter `CycleSlotId`,
`input_snapshot_hash`, or any semantic work identity.

## Frozen semantic work persistence

Semantic outcomes belong to the Slot, not to an Attempt.

`CycleWorkKind` is a `StrEnum` with exactly:

```text
EMERGENCY_POSITION | ORDINARY_CLOSE_POSITION | ENTRY_PAIR
INTENT_DISPATCH    | PAPER_COMPLETION
```

The work root key is frozen per kind:

| kind | `work_root_key` |
|---|---|
| `EMERGENCY_POSITION` | `paper_position_id` |
| `ORDINARY_CLOSE_POSITION` | `paper_position_id` |
| `ENTRY_PAIR` | `pair.symbol` |
| `INTENT_DISPATCH` | `<intent_kind>:<source_intent_id>` |
| `PAPER_COMPLETION` | `<intent_kind>:<source_intent_id>` |

where `<intent_kind>` is the M3 `PaperIntentKind` value and `<source_intent_id>` is
`intent_id.value` for `ENTRY`/`EMERGENCY_LIQUIDATION` and `idempotency_key` for
`ORDINARY_CLOSE`, matching M3's `PaperOrderIntentLineage.source_intent_id`.

`live_cycle_work_results` has `UNIQUE(cycle_slot_id, work_kind, work_root_key)`. That
constraint is what makes "the same semantic work cannot belong to two results"
DB-enforced. Its identity is
`"cycle-work-" + digest({cycle_slot_id, work_kind, work_root_key, result_code,
result_payload})` and it is append-or-compare: a second write with the same root and
different content fails closed.

A work result row exists only when that root is **terminal**. A nonterminal state is
represented by the absence of a row, so no row is ever updated. A later Attempt reuses
every existing row.

`CycleCompletion` is one row per Slot (`UNIQUE(cycle_slot_id)`), written only when
every expected work root of the Slot has a result row. The expected root inventory is
derived deterministically:

- when a `live_cycle_emergency_risk_decisions` row exists for the Slot:
  one `EMERGENCY_POSITION` root per frozen open position, plus one `INTENT_DISPATCH`
  and one `PAPER_COMPLETION` root per emergency intent;
- otherwise: one `ORDINARY_CLOSE_POSITION` root per frozen open position, one
  `ENTRY_PAIR` root per configured Pair, plus one `INTENT_DISPATCH` and one
  `PAPER_COMPLETION` root per approved intent recorded by those results.

The completion transaction recomputes that inventory from persisted rows only and
fails closed when a root is missing.

## Frozen one-account incomplete-slot rule

M4 v1 supports one Paper account. While an incomplete Slot exists for that account, a
different new Slot cannot be claimed; the old Slot must be resumed and completed
first. This is enforced twice:

- schema: a `BEFORE INSERT ON live_cycle_slots` trigger that aborts when another row
  in `live_cycle_slots` has the same `paper_account_id` and has no matching row in
  `live_cycle_completions`;
- transaction: the freeze transaction explicitly runs the same predicate under
  `BEGIN IMMEDIATE` before inserting the slot row, so the rejection is a typed
  fail-closed error rather than a raw SQLite abort.

Resuming the same Slot inserts no new `live_cycle_slots` row and is unaffected. This
is an operational persistence rule, not a scheduler lock or a process-level lock; M5
owns recurring scheduling and process overlap control.

## Frozen persistence model

Live migration `0007_operational_paper_cycle.sql` is the next available additive
number after `0006`. Migrations `0001` through `0006` are not edited. No Signal Store
migration is added.

All new tables are prefixed `live_cycle_` and every one of them has UPDATE and DELETE
rejection triggers.

```text
policies                    PK cycle_policy_version; policy_content_id NOT NULL;
                            policy_json NOT NULL
slots                       PK cycle_slot_id; scheduled_for; as_of;
                            execution_authority_mode; strategy_id; strategy_version;
                            strategy_config_identity;
                            cycle_policy_version REFERENCES policies;
                            paper_account_id; created_at;
                            BEFORE INSERT trigger: one incomplete slot per account
input_snapshots             PK cycle_input_snapshot_id;
                            cycle_slot_id TEXT NOT NULL UNIQUE REFERENCES slots;
                            input_snapshot_hash TEXT NOT NULL UNIQUE;
                            signal_store_checkpoint_sequence INTEGER NOT NULL;
                            paper_account_id; paper_account_state_kind
                              CHECK IN ('BOOTSTRAP_ONLY','SNAPSHOT');
                            paper_account_snapshot_id;
                            paper_reconciliation_result_id;
                            highest_application_seq; highest_ledger_entry_seq;
                            highest_order_event_seq;
                            account_equity TEXT NOT NULL;
                            account_used_margin TEXT NOT NULL;
                            snapshot_json TEXT NOT NULL; captured_at TEXT NOT NULL
input_pair_lineage          PK (cycle_input_snapshot_id, pair_ordinal);
                            UNIQUE(cycle_input_snapshot_id, pair);
                            every Pair lineage column frozen above
input_positions             PK (cycle_input_snapshot_id, position_ordinal);
                            UNIQUE(cycle_input_snapshot_id, paper_position_id)
input_open_orders           PK (cycle_input_snapshot_id, order_ordinal);
                            UNIQUE(cycle_input_snapshot_id, paper_order_id)
attempt_starts              attempt_seq INTEGER PRIMARY KEY AUTOINCREMENT;
                            UNIQUE(cycle_attempt_start_id);
                            cycle_slot_id REFERENCES slots;
                            cycle_input_snapshot_id REFERENCES input_snapshots;
                            worker_identity; started_at
attempt_terminals           PK cycle_attempt_terminal_id;
                            cycle_attempt_start_id TEXT NOT NULL UNIQUE
                              REFERENCES attempt_starts(cycle_attempt_start_id);
                            outcome CHECK IN
                              ('COMPLETED','INCOMPLETE','FAILED_INTEGRITY');
                            failure_classification; completed_at
work_results                PK cycle_work_result_id;
                            cycle_slot_id REFERENCES slots;
                            work_kind CHECK IN ('EMERGENCY_POSITION',
                              'ORDINARY_CLOSE_POSITION','ENTRY_PAIR',
                              'INTENT_DISPATCH','PAPER_COMPLETION');
                            work_root_key; result_code; result_json; created_at;
                            UNIQUE(cycle_slot_id, work_kind, work_root_key);
                            BEFORE INSERT trigger: emergency/ordinary+entry exclusion
emergency_risk_decisions    PK emergency_risk_decision_id;
                            cycle_slot_id TEXT NOT NULL UNIQUE REFERENCES slots;
                            risk_policy_version; equity; used_margin; margin_ratio;
                            minimum_margin_ratio; created_at
emergency_liquidation_intents
                            PK execution_intent_id;
                            emergency_risk_decision_id REFERENCES
                              emergency_risk_decisions;
                            paper_position_id; pair; quantity;
                            idempotency_key TEXT NOT NULL UNIQUE;
                            created_at;
                            UNIQUE(emergency_risk_decision_id, paper_position_id)
entry_portfolio_decisions   PK portfolio_decision_id;
                            cycle_slot_id REFERENCES slots;
                            production_candidate_id REFERENCES
                              live_production_trade_candidates(candidate_id);
                            UNIQUE(cycle_slot_id, production_candidate_id);
                            disposition CHECK IN ('ACCEPT','REDUCE','REJECT');
                            requested_quantity; reference_price; proposed_quantity;
                            reason_code; exposure_snapshot_json; created_at
entry_risk_decisions        PK risk_decision_id;
                            portfolio_decision_id TEXT NOT NULL UNIQUE REFERENCES
                              entry_portfolio_decisions;
                            disposition CHECK IN ('APPROVE','REJECT');
                            reason_code; risk_policy_version;
                            account_margin_ratio_kind CHECK IN
                              ('NO_USED_MARGIN','RATIO');
                            account_margin_ratio; created_at
approved_execution_intents  PK execution_intent_id;
                            risk_decision_id TEXT NOT NULL UNIQUE REFERENCES
                              entry_risk_decisions;
                            production_candidate_id REFERENCES
                              live_production_trade_candidates(candidate_id);
                            pair; side; quantity;
                            idempotency_key TEXT NOT NULL UNIQUE; created_at
completions                 PK cycle_completion_id;
                            cycle_slot_id TEXT NOT NULL UNIQUE REFERENCES slots;
                            work_result_count INTEGER NOT NULL; completed_at
```

Every monetary and quantity column is `TEXT` holding the exact `str(Decimal)` value;
no `REAL` column exists. Every timestamp column is `TEXT` holding an ISO-8601 UTC
value.

Frozen transactions, each `BEGIN IMMEDIATE`, each authenticating every persisted
parent by full content before writing and re-reading what it wrote before commit:

- **C1 claim and freeze** — the ordered steps in "Frozen atomic Cycle snapshot
  freeze".
- **C2 attempt start** — one `attempt_starts` row.
- **C3 attempt terminal** — one `attempt_terminals` row for an existing start with no
  terminal; a second terminal for one start fails closed.
- **C4 work result** — one `work_results` row, append-or-compare, requiring the Slot
  to have a snapshot and passing the exclusion trigger.
- **C5 emergency chain** — one `emergency_risk_decisions` row plus every
  `emergency_liquidation_intents` row for the Slot, append-or-compare, in one
  transaction.
- **C6 entry chain** — one `entry_portfolio_decisions` row, its
  `entry_risk_decisions` row, and, only on Risk `APPROVE`, one
  `approved_execution_intents` row, in one transaction, after authenticating the
  `live_production_trade_candidates` parent row by full content.
- **C7 completion** — recompute the expected work-root inventory from persisted rows,
  require every root to have a result, insert one `completions` row.

Read-only hydration methods (no `BEGIN`, no writes): `hydrate_input_snapshot`,
`list_work_results`, `hydrate_emergency_chain`, `hydrate_entry_chain`,
`hydrate_completion`, `has_incomplete_slot_for_account`.

Any conflict, corruption, missing parent, or injected failure rolls back the entire
transaction and leaves no partial rows. There is no repair path.

## B1 — Cycle contracts, identity and explicit policy

Scope: the pure immutable Cycle domain. Add `CycleSlotId` derivation and
`CycleSlot`, `OperationalPaperCyclePolicy` with its full field list and
content-addressed `policy_content_id`, `CycleMarkObservation`, `CycleMarkOutcome`,
`CycleMarkResolution`, the `CycleInputSnapshot` root plus its ordered Pair, position,
and open-order lineage records, `CycleAccountStateKind`, `CycleAccountRiskView`,
`CycleWorkKind`, the frozen work-root-key derivation, `CycleWorkResultCode`,
`CycleAttemptOutcome`, `CycleAttemptStart`, `CycleAttemptTerminal`,
`CycleCompletion`, the canonical `input_snapshot_hash` computation, and the
deterministic entry/emergency identity derivations.

Expected implementation surface: new
`apps/swap_bot/src/swap_bot/cycle/__init__.py` and
`apps/swap_bot/src/swap_bot/cycle/contracts.py`; focused tests under
`tests/operational_cycle_domain/`; new architecture tripwire cases for the `cycle`
package; `docs/08_TEST_STRATEGY.md` and ExecPlan 0006 Progress.

B1 must not add: any store, SQL, migration, `Clock` use, adapter, Port, Portfolio or
Risk call, Strategy call, Paper call, CLI, or import of `sqlite3`,
`live_migrations`, `fx_research`, `execution`, `ports`, `shadow`, or `llm_feature`.

## B2 — Operational adapters, shared Signal batch claim and immutable input freeze

Scope: the Live-owned input Ports and the connection-scoped selection that the freeze
transaction runs.

- New Ports: `CycleMarkSource.observe(pair, *, as_of) -> CycleMarkObservation | None`,
  `OperationalSwapSource.resolve(pair, *, as_of) -> OperationalSwapResolution`, and
  `PaperFillObservationSource.observe(pair, *, not_before) -> tuple[PaperMarketObservation, ...]`.
  Three distinct Ports with three distinct output types; no adapter implements two of
  them.
- Additive `SQLiteSignalStore.claim_pair_signal_materializations(...)` batch first
  claim and `SQLiteSignalStore.get_pair_signal_materialization_result(...)` read-only
  hydration, both exactly as frozen above, with the existing single-Request method
  delegating to the same connection-scoped helper and keeping its exact behaviour.
- Additive connection-scoped Adoption authorization: `authorize_signal_on(connection,
  signal, *, strategy_id, strategy_version, strategy_config_identity, runtime_mode,
  authorized_at) -> AuthorizedSignal` in `adoption_gate.py`, holding the complete gate
  logic, plus the connection-scoped `SQLiteAdoptionStore` helpers it needs
  (`list_approvals_on`, `is_revoked_at_on`, `find_authorization_on`,
  `append_authorization_on`) and one public `open_connection()`.
  `LiveAdoptionGate.authorize` is reimplemented as a thin wrapper that opens one
  `BEGIN IMMEDIATE` transaction and delegates, so exactly one implementation exists
  and M2-C/M2-D behaviour is preserved.
- New `cycle/adapters.py` (the three Ports and the typed mark resolution rule) and
  `cycle/inputs.py` (the read-only connection-scoped selectors that produce the
  account state class, the account snapshot by boundary equality, the ordered open
  positions, the ordered nonterminal orders, and the reconciliation-result
  authentication, plus the per-Pair Signal/Adoption/Swap authentication and
  reconstruction helpers).

Expected implementation surface: new `cycle/adapters.py`, `cycle/inputs.py`; additive
methods in `packages/fx_signal_store/src/fx_signal_store/store.py`,
`apps/swap_bot/src/swap_bot/adoption_gate.py`, and
`apps/swap_bot/src/swap_bot/adoption_store.py`; additive exports in `cycle/__init__.py`
and `packages/fx_signal_store/src/fx_signal_store/__init__.py`; focused tests under
`tests/operational_cycle_inputs/` and `tests/pair_signal_materialization/`; tripwire
cases for the new modules; `docs/05_DATA_AND_VERSIONING.md`,
`docs/08_TEST_STRATEGY.md`, and ExecPlan 0006 Progress.

B2 must not add: any Live migration, any write to a `live_cycle_*` table, any
`BEGIN`/`COMMIT`/`ROLLBACK` or `INSERT` in `cycle/inputs.py`, any Signal Store
migration, any change to an existing Signal Store or Adoption public signature, any
Portfolio/Risk/Strategy/Paper call, any `Clock`, any CLI, or any automatic retry.

## B3 — Emergency/ordinary-close/entry allocation and Risk decision domain

Scope: the pure deterministic decision domain over a frozen snapshot. Add the
`CycleAccountRiskView` derivation and the emergency predicate, the deterministic
`ApprovedLiquidationIntent` construction, the frozen deterministic ordering of
positions and Pairs, the transient legacy `TradeCandidate` bridge, the
`PositionSnapshot`/`PendingIntent` exposure-input builders including the
earlier-in-cycle pending contributions, the `OrdinaryPositionExitWorkItem` builder,
the frozen entry precedence, the deterministic Portfolio/Risk/intent identity
derivations, and the typed `ENTRY_PAIR`/`ORDINARY_CLOSE_POSITION`/
`EMERGENCY_POSITION` result codes.

Expected implementation surface: new
`apps/swap_bot/src/swap_bot/cycle/decisions.py`; additive exports in
`cycle/__init__.py`; focused tests under `tests/operational_cycle_decisions/`;
tripwire case for the new module; `docs/04_SWAP_BOT.md`,
`docs/08_TEST_STRATEGY.md`, and ExecPlan 0006 Progress.

B3 must not add: any store, SQL, migration, `Clock`, adapter call, Paper call, CLI,
`sqlite3` import, new Portfolio or Risk rule, new margin threshold, change to
`TradeCandidate`, `PositionSnapshot`, `PendingIntent`, `AccountSnapshot`,
`PortfolioService`, `RiskService`, `RiskPolicy`, `ProductionTradeCandidate`, or any
M2-D contract, or any write to a legacy entry table.

## B4 — Migration `0007`, `SQLiteOperationalCycleStore`, attempts and recovery

Scope: additive Live migration `0007_operational_paper_cycle.sql` and one SQLite
Cycle store implementing the seven frozen transactions C1 through C7 plus the
read-only hydration methods, with append-or-compare persistence, full parent
authentication, hydrate-and-compare retry, the one-account incomplete-slot trigger and
its transactional counterpart, the emergency/ordinary-close exclusion trigger, the
`UNIQUE(cycle_slot_id, work_kind, work_root_key)` semantic-work constraint, and the
deterministic completion inventory.

C1 calls the B2 read-only selectors and `authorize_signal_on` on its own connection,
so every Live-owned selection is inside the freeze transaction.

Expected implementation surface: new
`apps/swap_bot/src/swap_bot/migrations/0007_operational_paper_cycle.sql`; new
`apps/swap_bot/src/swap_bot/cycle/store.py`; additive exports in `cycle/__init__.py`;
the updated exact migration-filename assertion in
`tests/architecture/test_import_boundaries.py`; focused tests under
`tests/operational_cycle_persistence/`; `docs/05_DATA_AND_VERSIONING.md`,
`docs/08_TEST_STRATEGY.md`, and ExecPlan 0006 Progress.

`cycle/store.py` is the only `cycle` module permitted to import `live_migrations` or
to execute a write statement. B4 must not edit migrations `0001` through `0006`, must
not add an application service, adapter, CLI, or `Clock`, must not add automatic
retry, repair, `INSERT OR IGNORE` used as proof of equality, or any write outside the
frozen table list, and must not write to any `live_paper_*`, M2-C, or M2-D table other
than the append-or-compare `SQLiteOperationalSwapStore.append_or_compare_on` call C1
already performs.

## B5 — `OperationalPaperCycleService`, post-intent dispatch, `paper-once` CLI

Scope: one application service with exactly one public entry point and the thin CLI.

```python
class OperationalPaperCycleService:
    def __init__(
        self,
        *,
        cycle_store: SQLiteOperationalCycleStore,
        signal_store: SQLiteSignalStore,
        materializer: OperationalPairSignalMaterializer,
        entry_store: SQLiteProductionEntryStore,
        ordinary_close_service: OrdinaryCloseApplicationService,
        paper_store: SQLitePaperStore,
        paper_service: PaperApplicationService,
        mark_source: CycleMarkSource,
        swap_source: OperationalSwapSource,
        fill_observation_source: PaperFillObservationSource,
        clock: Clock,
        worker_identity: str,
    ) -> None: ...

    def run_once(
        self,
        *,
        config: NewsFilteredCarryStrategyConfig,
        policy: OperationalPaperCyclePolicy,
        authority: ExecutionAuthorityMode,
        scheduled_for: datetime,
        as_of: datetime,
    ) -> CycleRunResult: ...
```

`run_once` performs exactly one pass:

```text
reject LIVE (first statement, before any Clock or store call)
-> validate policy against config
-> read the Clock exactly once -> attempt_instant
-> hydrate the existing snapshot for CycleSlotId, or:
     batch-claim both Signal Requests -> materialize both Pairs
     -> resolve both Swaps -> resolve both Cycle marks
     -> classify and (established account) reconcile Paper state
     -> C1 claim slot and freeze snapshot
-> C2 attempt start
-> emergency (C5 + per-position C4) OR (ordinary close per position -> entry per Pair)
-> dispatch every approved intent once (C4 per dispatch root)
-> C7 completion when every expected root is terminal
-> C3 attempt terminal
-> typed CycleRunResult
```

`CycleRunResult` reports `outcome` in `{COMPLETED, INCOMPLETE}`, the
`cycle_slot_id`, `cycle_input_snapshot_id`, `cycle_attempt_start_id`, whether the
snapshot was newly frozen or reused, the ordered typed work results, and whether a
`CycleCompletion` row exists.

CLI, added to the existing `apps/swap_bot/src/swap_bot/__main__.py`:

```text
python -m swap_bot paper-once
    --live-database <path>
    --signal-database <path>
    --cycle-policy <path to reviewed JSON>
    --strategy-config <path to reviewed JSON>
    --authority SHADOW_NOT_SUBMITTED|PAPER|LIVE
    --scheduled-for <UTC ISO-8601>
    --as-of <UTC ISO-8601>
    --worker-identity <non-blank str>
```

Both `--scheduled-for` and `--as-of` are required and explicit; M4 documents no
derivation between them. The two configuration files are required and fully explicit;
no fixture value, Research default, or code default is promoted. `--authority LIVE`
is accepted by the parser and rejected by the authority guard before any store,
adapter, `Clock`, or Paper object is constructed, so the rejection is observable
rather than an argparse artifact. The command performs exactly one `run_once` call and
prints one JSON summary. It contains no loop, daemon, recurring schedule, automatic
retry, sleep, backoff, or burn-in report.

Expected implementation surface: new
`apps/swap_bot/src/swap_bot/cycle/application.py` (the service, the typed result, and
the two reviewed-configuration loaders); additive subcommand wiring in
`apps/swap_bot/src/swap_bot/__main__.py`; additive exports in `cycle/__init__.py`;
focused tests under `tests/operational_cycle_application/`,
`tests/operational_cycle_recovery/`, and `tests/broker_contract/`; tripwire case for
the new module and the runtime zero-Broker probe; `docs/README.md`,
`docs/01_ARCHITECTURE.md`, `docs/04_SWAP_BOT.md`, `docs/06_REPOSITORY_STRUCTURE.md`,
`docs/08_TEST_STRATEGY.md`, and ExecPlan 0006 Progress.

B5 must not add: a recurring loop, daemon, scheduler, process-level overlap lock,
sleep, backoff, automatic retry, health or metrics emission, alerting, burn-in or
readiness report, a second `Clock` read per `run_once`, a caller-supplied evaluation
or audit instant, a second public entry point, multi-account support, a real Broker
adapter, or any new SQL, migration, or persistence behaviour.
