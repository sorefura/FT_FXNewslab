# M4 Frozen Acceptance Criteria

Status: frozen acceptance input for the Phase Goal workflow.

M4 is accepted only when all of the following are true.

## Unit gates

- B1, B2, B3, B4, and B5 completed in order.
- Every B unit is approved by a newly created `phase_reviewer` identity.
- Every rejection is corrected and reviewed by another new reviewer identity.
- A new `phase_final_reviewer` approves the complete Phase after full checks.
- `docs/phases/M3.toml` and every file under `docs/phases/M3/` are byte-identical to
  the baseline commit.

## Slot identity evidence

- `cycle_slot_id` is recomputed from exactly the seven frozen fields. A test changes
  each of `scheduled_for`, `as_of`, `execution_authority_mode`, `strategy_id`,
  `strategy_version`, `strategy_config_identity`, and `cycle_policy_version`
  individually and proves each one changes the ID.
- A test changes each excluded value individually — every Signal Request ID, the
  Signal Store checkpoint, every Adoption/Swap/mark/Position/Account/open-order input
  ID, `captured_at`, `attempt_instant`, `worker_identity`, the attempt IDs, and
  `input_snapshot_hash` — and proves the `cycle_slot_id` is unchanged in every case.
- Two runs whose only difference is the discovered input therefore claim the same
  Slot, proved end to end by running `run_once` twice with different late-arriving
  Signals and asserting one `live_cycle_slots` row.
- A naive or non-UTC `scheduled_for` or `as_of` fails closed before any durable write.

## One Slot, one snapshot evidence

- After any number of `run_once` calls for one Slot, `live_cycle_slots` and
  `live_cycle_input_snapshots` each hold exactly one row for that Slot, proved by
  counting rows, not by a returned flag.
- `live_cycle_input_snapshots.cycle_slot_id` is UNIQUE: a direct second insert with a
  different `cycle_input_snapshot_id` for the same Slot is rejected by the database.
- A forced conflicting second snapshot (a fixture that mutates one frozen input and
  bypasses the hydrate-first path) fails closed atomically: the transaction rolls
  back, and `live_cycle_input_snapshots`, `live_cycle_input_pair_lineage`,
  `live_cycle_input_positions`, and `live_cycle_input_open_orders` gain zero rows.
- A persisted snapshot whose recomputed `input_snapshot_hash` differs from its stored
  value is an integrity failure on the hydrate path and starts no attempt.
- `input_snapshot_hash` is identical across two attempts whose `Clock` returns
  different instants, proving `captured_at` is excluded from the semantic hash.

## Shared Signal checkpoint evidence

- One `run_once` produces exactly two `pair_signal_materialization_claims` rows whose
  `checkpoint_sequence` values are equal and whose `captured_at` values are equal,
  proved by reading the Signal Store rows.
- A Signal appended between the two Pair claims cannot change the second Pair's
  checkpoint: a fixture appends a Signal from a second connection while the batch
  transaction is open, and the second claim still carries the first checkpoint. A run
  in which the two claims carry different checkpoints fails the test.
- `claim_pair_signal_materializations` evaluates `MAX(store_sequence)` and the catalog
  integrity validation exactly once per batch, proved by a counting instrumentation
  double or by asserting a single `BEGIN IMMEDIATE` per call.
- An empty request tuple, a duplicate `request_id` in the request tuple, or a
  `captured_at` earlier than any Request `as_of` fails closed before the transaction
  opens and writes zero rows.
- A failure injected at the second Request's Specification, Request, or Claim insert
  rolls the whole batch back: neither Pair has a Claim afterwards.
- The existing single-Request `claim_pair_signal_materialization` keeps its exact
  signature, transaction boundary, return type, and error types; every existing
  `tests/pair_signal_materialization` and `tests/signal_store` test passes unchanged,
  and a test asserts that a batch of one produces a Claim identical to the one the
  single-Request method produces for the same Request and `captured_at`.
- `get_pair_signal_materialization_result` opens no writer transaction and writes
  nothing, proved by comparing every Signal Store table row count before and after,
  and returns `None` for a Request that was never claimed. A Claim without a persisted
  Selection or Completion raises `SignalStoreIntegrityError`.
- The `PairSignalMaterializerResult` returned by the read-only hydration is equal
  field by field to the one the materializer returned on the freezing attempt.

## Snapshot atomicity and no-reselection evidence

- Adoption authorization happens on the freeze transaction's own connection: a test
  patches `SQLiteAdoptionStore.open_connection`, `LiveAdoptionGate.authorize`,
  `SQLiteAdoptionStore.list_approvals`, `SQLiteAdoptionStore.find_authorization`, and
  `SQLiteAdoptionStore.append_authorization` to raise, and a complete first-claim run
  still succeeds — proving the freeze path uses only the connection-scoped helpers.
- A failure injected after the authorization insert but before the snapshot insert
  rolls back the authorization too: `live_signal_authorizations` gains zero rows and
  no snapshot exists.
- `authorize_signal_on` and `LiveAdoptionGate.authorize` produce identical
  `AuthorizedSignal` values for identical inputs across all approval, revocation,
  expiry, not-yet-effective, ambiguity, specification-mismatch, and
  no-approval branches, so the delegation introduces no divergence.
- After the snapshot exists, a retry makes zero source-selection calls: a probe
  replaces `CycleMarkSource.observe`, `OperationalSwapSource.resolve`,
  `OperationalPairSignalMaterializer.materialize`,
  `SQLiteSignalStore.claim_pair_signal_materializations`,
  `LiveAdoptionGate.authorize`, `authorize_signal_on`, and
  `SQLitePaperStore.reconcile_account` with doubles that fail on call, and the retry
  completes normally.
- Late, backfilled, or corrected evidence never replaces the snapshot, proved
  individually for six sources: a newer Pair Signal, a newer/revoked Adoption
  decision, newer Swap evidence, a newer Cycle mark, a new Paper position, and a new
  Paper account snapshot. In every case the retry's persisted snapshot rows and
  `input_snapshot_hash` are byte-identical to the first freeze, and the decision
  stages use the frozen values.
- A revocation decided after `captured_at` does not change the frozen authorization;
  a revocation decided at or before `captured_at` that contradicts the frozen
  authorization is detected by the M2-C B4 store's own re-authentication and fails
  closed rather than selecting a replacement.
- Every stage that reads a frozen record proves the record it reads equals the frozen
  lineage: a tampered `live_signal_authorizations`, `live_operational_swap_evidence`,
  or `live_production_trade_candidates` row makes the retry fail closed and adds no
  semantic row.

## Market evidence role separation

- `CycleMarkObservation` and `PaperMarketObservation` are unrelated types: neither is
  a subclass of the other, no conversion function exists in the repository, and
  passing a `CycleMarkObservation` to any `PaperApplicationService` entry point or to
  `SQLitePaperStore.append_market_observations` raises before any store work.
- `CycleMarkSource` and `PaperFillObservationSource` are distinct Protocols with
  distinct return types, and a static assertion proves no class in the repository
  implements both.
- A frozen `CycleMarkObservation` never appears in `live_paper_market_observations`:
  after a complete PAPER run, every row of that table is traceable to a
  `PaperFillObservationSource` return value.
- Pre-intent market evidence cannot fill: a `PaperFillObservationSource` that returns
  an observation with `received_at < intent.created_at` is an integrity failure that
  writes no Fill; and a fixture that persists such an observation directly still
  produces no Fill because M3's eligibility clause 3 rejects it.
- M3's Step window, due boundary, freshness, local-availability, and earlier-Step
  exclusion rules continue to govern every M4 Fill: a case per rule proves an
  observation rejected by that rule produces a PENDING attempt or a terminal
  `NO_MARKET`, never a Fill, when driven through `run_once`.
- The `CycleMarkOutcome` values are each produced by their own case: adapter returns
  `None` -> `MISSING`; `as_of - provider_observed_at` one microsecond beyond
  `cycle_mark_maximum_age` -> `STALE`, exactly equal -> `OBSERVED`.
- A mark whose `pair`, `source`, or `source_version` differs from the request, or
  whose `received_at > as_of`, is an integrity failure and writes no snapshot.

## Paper reconciliation gate evidence

- On a fresh Paper account (no rows in `live_paper_positions`, `live_paper_orders`,
  `live_paper_ledger_entries`), the freeze performs no reconciliation, writes zero
  `live_paper_*` rows, and records `paper_account_state_kind = BOOTSTRAP_ONLY` with
  `account_equity = bootstrap.initial_cash` and `account_used_margin = 0`.
- On an established account, the freeze requires a `MATCHED`
  `PaperReconciliationResult` whose three boundaries equal the account's current
  maxima. Tampering with any one of a position fill application, a ledger entry, a
  position snapshot, or an account snapshot makes reconciliation `MISMATCHED` and
  blocks the Cycle claim: zero `live_cycle_slots`, `live_cycle_input_snapshots`, and
  `live_cycle_attempt_starts` rows are written.
- A malformed persisted Paper row (a corrupted `Decimal` text, a broken lineage
  reference, an illegal order-event sequence) raises through M3's own rebuild and
  blocks the Cycle claim the same way.
- Inserting any Paper row between the reconciliation call and the freeze transaction
  makes the in-transaction boundary comparison fail and blocks the claim, proving the
  rebuild covers exactly the frozen state.
- The frozen account snapshot is selected by exact equality on
  `(highest_application_seq, highest_ledger_entry_seq)`, never by `ORDER BY ... DESC
  LIMIT 1`: a fixture with several account snapshots at different boundaries proves
  the selected one, and an SQL scan of `cycle/` finds no `DESC LIMIT 1` account or
  position lookup.
- A non-zero boundary with no matching account snapshot, or more than one matching
  row, is an integrity failure.
- The frozen open-position quantities, the frozen nonterminal-order remaining
  quantities, and the frozen projected order states are all derived from persisted
  application/plan/Step/Fill/event lineage; a test mutates nothing but adds rows
  beyond the boundary and proves the frozen values are unchanged.
- An order with no order event at or below the boundary is excluded before any
  projection, so an older snapshot never raises a projection integrity error.

## Typed business-result evidence

- Each of these becomes a typed terminal `CycleWorkResult` and lets the Cycle continue
  to later independent work, proved individually: mark `MISSING`, mark `STALE`,
  materializer `NO_SELECTION`, materializer `AMBIGUOUS`, adoption inactive, Swap
  missing, Swap malformed, entry Strategy skip (each `EntrySkipReason` reachable from
  frozen evidence), Portfolio `REJECT`, Risk `REJECT`, ordinary-close `KEEP` (each
  reachable keep reason), ordinary-close Portfolio `REJECT`, and ordinary-close Risk
  `REJECT`.
- None of those cases writes a `live_paper_*` order/fill/ledger row and none of them
  prevents the other configured Pair or the other frozen Position from being
  processed.
- Every one of them still allows the Slot to reach `CycleCompletion` when no
  non-terminal Paper work remains.
- No business case is ever raised as an integrity error, and no integrity case is ever
  recorded as a business result; the two are proved distinct for the mark, Signal,
  Swap, Position, Account, and open-order inputs.

## Authority evidence

- `LIVE` is rejected with zero work: a probe patches `Clock.now`,
  `SQLiteSignalStore.__init__`, `SQLiteOperationalCycleStore.__init__`,
  `SQLitePaperStore.__init__`, `PaperApplicationService.__init__`,
  `CycleMarkSource.observe`, `OperationalSwapSource.resolve`,
  `PaperFillObservationSource.observe`, `GmoPrivatePostTransport.__init__`,
  `GmoPrivatePostTransport.post_once`, `ExecutionService.submit`, and
  `LiveArmPolicy.is_armed`, runs both `run_once` and the `paper-once` CLI with
  `--authority LIVE`, and observes exactly zero calls to each and zero rows in every
  `live_cycle_*` and `live_paper_*` table.
- `SHADOW_NOT_SUBMITTED` never constructs or calls `PaperApplicationService`, proved
  by a probe on its `__init__` and its three entry points.
- `SHADOW_NOT_SUBMITTED` on a fresh account leaves every `live_paper_*` table at
  exactly zero rows, proved by counting rows in every table individually.
- `SHADOW_NOT_SUBMITTED` on an established account writes exactly one
  `live_paper_reconciliation_results` row and zero rows in every other `live_paper_*`
  table, proved the same way; the row count is asserted per table, not summarised.
- `SHADOW_NOT_SUBMITTED` still persists the Cycle snapshot, the Strategy evaluation,
  the entry Portfolio and Risk decisions, the approved intents, the work results, and
  the completion.
- `PAPER` creates Paper evidence only from exact approved intents of this Slot: every
  `live_paper_orders` row's `intent_lineage.source_intent_id` matches an intent
  recorded in this Slot's persisted emergency or entry chain or in an
  `OrdinaryCloseApplicationService` result of this Slot.
- Every file under `apps/swap_bot/src/swap_bot/cycle/` imports no `fx_research`,
  `openai`, `execution`, `ports`, `shadow`, or `llm_feature` module, and no name
  `BrokerGateway`, `ExecutionService`, `GmoPrivatePostTransport`, `LiveArmPolicy`, or
  `HttpClient` appears as an imported or referenced symbol.
- Only `cycle/store.py` and `cycle/inputs.py` import `sqlite3`; only
  `cycle/store.py` imports `live_migrations`; `cycle/inputs.py` contains no `BEGIN`,
  `COMMIT`, `ROLLBACK`, `INSERT`, `UPDATE`, or `DELETE` statement.
- A runtime probe patches `GmoPrivatePostTransport.__init__`, `post_once`,
  `ExecutionService.submit`, and `LiveArmPolicy.is_armed`, runs a complete emergency
  Cycle, a complete ordinary-close-plus-entry Cycle, and the `paper-once` CLI under
  `PAPER`, and observes exactly zero calls to each.
- A Paper order, Fill, or completion never changes `LiveArmPolicy`, arming environment
  state, Adoption `RuntimeMode`, or any existing Live table other than the additive
  writes this specification names.

## Emergency and cycle-order evidence

- `used_margin == 0` is never an emergency, proved at `equity` values above, at, and
  below every threshold.
- With `used_margin > 0`, `margin_ratio = equity / used_margin` is computed in
  `PAPER_QUOTIENT_ARITHMETIC_V1`; the value is asserted against an independently
  written expected `Decimal` literal, not by re-running the implementation formula.
- `margin_ratio` strictly below `RiskPolicy.minimum_margin_ratio` triggers emergency;
  exact equality does not. Both cases are tested at a one-unit-in-the-last-place
  distance.
- When emergency triggers, exactly one `ApprovedLiquidationIntent` per frozen open
  Position exists, each with the frozen `position_id`, `pair`, `quantity`, the Slot's
  `captured_at`, and `risk_decision_id` equal to the persisted
  `emergency_risk_decision_id`.
- `live_cycle_emergency_risk_decisions.cycle_slot_id` is UNIQUE and the row is a real
  persisted authority: deleting or omitting it makes the intent chain unwritable, and
  no legacy `live_portfolio_decisions` or `live_risk_decisions` row is created by M4.
- When emergency triggers, no ordinary-close evaluation and no entry evaluation
  occurs: probes on `OrdinaryCloseApplicationService.run` and
  `SQLiteProductionEntryStore.evaluate_and_persist` observe zero calls, and the Slot
  has zero `ORDINARY_CLOSE_POSITION` and zero `ENTRY_PAIR` work results.
- The `live_cycle_work_results` exclusion trigger is proved in both directions by
  direct inserts: an `EMERGENCY_POSITION` row after an `ENTRY_PAIR` row for the same
  Slot is rejected, and an `ORDINARY_CLOSE_POSITION` or `ENTRY_PAIR` row after an
  `EMERGENCY_POSITION` row for the same Slot is rejected.
- With no emergency, ordinary close strictly precedes entry: an ordering probe records
  the call sequence and asserts every `OrdinaryCloseApplicationService.run` call
  precedes every `SQLiteProductionEntryStore.evaluate_and_persist` call.
- Position processing order is exactly configured Pair ordinal then
  `paper_position_id` ascending, and Pair processing order is exactly
  `config.eligible_pairs` order. Both are proved with a fixture whose insertion order,
  `paper_position_id` order, and Pair order all disagree.
- Under `SHADOW_NOT_SUBMITTED`, emergency persists typed `NOT_SUBMITTED` semantic
  results only and calls no Paper entry point.

## Portfolio, Risk, and bridge evidence

- The second configured Pair's Portfolio evaluation sees the first Pair's approved
  intent: a fixture where Pair 1 consumes most of the shared-currency limit makes Pair
  2 `REDUCE` or `REJECT`, and removing Pair 1's pending contribution would make it
  `ACCEPT`. The exposure snapshot persisted for Pair 2 contains Pair 1's contribution.
- The second Pair also sees every ordinary-close approved intent created earlier in
  the same Cycle, proved by the same construction with a close intent instead.
- Every frozen nonterminal Paper order contributes exactly one `PendingIntent` with
  `quantity = remaining_quantity`, proved by a fixture with a `PARTIALLY_FILLED`
  order.
- Pending exposure is rebuilt from persisted evidence on a resumed attempt: a crash
  between Pair 1 and Pair 2 followed by a retry produces the identical Pair 2
  Portfolio decision ID and exposure snapshot.
- Entry requested quantity comes only from `policy.entry_quantities`: changing the
  Strategy config, the Candidate, the mark, or the account state does not change it,
  and a `ProductionTradeCandidate` carrying any quantity-like value is never read for
  quantity.
- Entry reference price is exactly `mark.ask` for BUY and `mark.bid` for SELL; open
  position `current_price` is exactly `mark.bid` for LONG and `mark.ask` for SHORT;
  each is asserted against the exact frozen `Decimal`, for both Pairs and both sides.
- The transient bridge is exact: its `candidate_id.value` equals the production
  `candidate_id`, its `strategy_id`/`strategy_version`/`pair`/`side`/`created_at`
  equal the production Candidate's, its `score` is the production `confidence` object
  itself, and its `signal_ids` is exactly `(production.signal_id,)`.
- `PairScore` is lossless and never clamped: a Candidate whose `pair_score` is outside
  `[0, 1]` and whose `confidence` differs from it is carried through the whole entry
  path, and the persisted `live_production_trade_candidates` JSON still holds the
  exact original `pair_score`. Any code path that constructs a `Probability` from a
  `PairScore` fails the test.
- `live_candidates` gains zero rows during every M4 run, proved by counting; the same
  is asserted for every other legacy REAL-score entry table.
- `portfolio_decision_id`, `risk_decision_id`, `execution_intent_id`, and the
  idempotency key are deterministic: two runs of the same Slot produce identical
  values, and changing the Slot, the Candidate, or the `cycle_policy_version` changes
  all four.
- `ApprovedExecutionIntent.quantity` equals the Portfolio `proposed_quantity` for both
  `ACCEPT` and `REDUCE`, and no intent exists for `REJECT` on either side.
- `account_margin_ratio_kind` is `NO_USED_MARGIN` exactly when `used_margin == 0` and
  `RATIO` otherwise, so the substituted legacy `margin_ratio` is never mistaken for an
  observed value.

## Ordinary close and entry persistence evidence

- Ordinary close uses the exact M2-D chain: the persisted
  `live_ordinary_close_portfolio_decisions`, `live_ordinary_close_risk_decisions`, and
  `live_ordinary_close_approved_intents` rows are created by
  `OrdinaryCloseApplicationService`, and M4 writes to no M2-D table directly.
- `ApprovedCloseIntent` keeps M2-D reservation semantics: the reservation snapshot the
  M2-D store builds includes every prior approved and unreleased intent for that
  Position, and two Cycles cannot reserve more than capacity.
- The `OrdinaryPositionExitWorkItem` is built only from frozen evidence: mutating any
  live source after the freeze does not change the work item ID on a retry, and the
  retry returns the persisted M2-D chain unchanged.
- A contradiction between frozen evidence and persisted M2-C/M2-D state (a tampered
  capacity row, a tampered authorization, a tampered Swap row) fails closed and never
  substitutes another input.
- Entry persistence still authenticates the exact M2-C roots: the
  `SQLiteProductionEntryStore` re-authentication of the persisted authorization,
  approval, policy, revocations, Strategy config, and Swap evidence is exercised, and
  each tampered parent fails closed with zero new rows.
- `ProductionEntryApplicationService` is never called by M4, proved by a probe on its
  `run`, so no post-freeze re-materialization or re-authorization can occur.
- The persisted `live_cycle_entry_portfolio_decisions.production_candidate_id`
  references an existing `live_production_trade_candidates` row; a decision naming a
  non-existent Candidate is rejected by the foreign key and by the store's own
  authentication.

## Semantic work, attempt, and completion evidence

- `UNIQUE(cycle_slot_id, work_kind, work_root_key)` is proved by direct conflicting
  inserts for each of the five `CycleWorkKind` values: the same semantic work cannot
  belong to two results.
- A second `append_work_result` for one root with different content fails closed; with
  identical content it is an exact reuse and adds no row.
- An Attempt Start may remain unterminated: a crash injected between C2 and C3 leaves
  one `live_cycle_attempt_starts` row with no
  `live_cycle_attempt_terminals` row, and that state is never repaired.
- A retry adds exactly one new `live_cycle_attempt_starts` row and zero new semantic
  rows: `live_cycle_work_results`, `live_cycle_emergency_*`, `live_cycle_entry_*`,
  every M2-C/M2-D table, and every `live_paper_*` table are compared row by row before
  and after.
- A second terminal for one Attempt Start is rejected by `UNIQUE`.
- `CycleCompletion` exists only when every expected work root has a result: a fixture
  removes one expected root's result and proves C7 fails closed and writes no
  completion row; restoring it lets C7 succeed.
- `live_cycle_completions.cycle_slot_id` is UNIQUE, and the completion's
  `work_result_count` equals the number of expected roots.
- The expected root inventory is recomputed from persisted rows only, proved by
  running C7 in a fresh process with no in-memory state.

## One-account incomplete-slot evidence

- With an incomplete Slot for the account, `run_once` for a different `CycleSlotId`
  fails closed with a typed error, writes no new `live_cycle_slots` row, and writes no
  snapshot.
- The same rejection is produced by a direct `INSERT INTO live_cycle_slots`, proving
  the rule is DB-enforced by the trigger and not only by the application.
- Resuming the incomplete Slot succeeds and, once it completes, the different Slot can
  be claimed.
- A pending or partially filled Paper order therefore cannot allow a new Slot: a
  fixture leaves a `PAPER_STEP_PENDING` intent, proves the Slot has no completion, and
  proves the next Slot claim is rejected.
- A frozen nonterminal Paper order that belongs to no Slot of this account
  participates in pending exposure and is not an expected work root, so it never
  deadlocks completion.

## Recovery evidence

Crash injection is exercised at every one of these named boundaries. In every case a
subsequent `run_once` for the same Slot converges to one logical result, creates no
duplicate semantic row, and reuses every first-write record:

- after the Signal batch Claims but before the Cycle snapshot;
- after the Cycle snapshot;
- after ordinary-close evaluation persistence;
- after the ordinary-close reservation and `ApprovedCloseIntent`;
- after the entry Candidate persistence;
- after the entry Portfolio decision;
- after the entry Risk decision;
- after the `ApprovedExecutionIntent`;
- after the Paper Order and Plan (M3 T1);
- after a PENDING attempt (M3 T4);
- after the market observation selection (M3 T3);
- after the Paper Fill;
- after the ledger, position, and account persistence;
- after reservation consumption or release;
- after a semantic `CycleWorkResult`;
- after the `CycleCompletion`.

Additionally:

- **Signal claims without a snapshot.** After a crash following the batch claim, the
  retry's `captured_at` equals the persisted Claim `captured_at` even though the
  `Clock` now returns a strictly later instant, and the frozen checkpoint is the
  original one.
- **Fill persisted but Cycle marker missing.** A crash after M3 wrote the Fill but
  before the `INTENT_DISPATCH`/`PAPER_COMPLETION` work result: the retry produces no
  second Fill, no second Step ordinal, no second terminal resolution, no second ledger
  entry, and no second reservation consumption, and it writes the missing work result.
- **Partial fill next Step.** A `FRACTION_OF_REMAINING` policy with
  `maximum_steps > 1` leaves Step 0 resolved with a positive remainder; a later retry
  reaches Step 1 with the exact `Decimal` remaining quantity derived from persisted
  Steps and Fills, and the Slot is `INCOMPLETE` until the order terminates.
- **Unresolved pre-due PENDING.** A retry before the Step due boundary with a newly
  eligible quote resolves the same Step, updates no PENDING attempt row, and creates
  exactly one selection.
- **Terminal Paper state.** Once the Paper order is terminal, a retry creates no extra
  Step, no extra attempt, no extra selection, and no extra Fill, and it does not call
  the `PaperFillObservationSource` for that dispatch root.
- **Completed Slot exact replay.** Running `run_once` again for a Slot that already
  has a `CycleCompletion` adds exactly one `live_cycle_attempt_starts` row and one
  `live_cycle_attempt_terminals` row and zero semantic rows anywhere, and returns
  `COMPLETED` with the same work results.
- Each recovery case asserts row counts per table before and after, not a summary
  field.

## Persistence and migration evidence

- Live migration `0007_operational_paper_cycle.sql` is additive and does not alter
  migrations `0001` through `0006` or any Signal Store migration; the architecture
  test asserts the exact seven-file Live migration set.
- Fresh database, upgrade from a database at `0006`, reopen and rerun, migration
  body/marker failure rollback and retry, and concurrent initialization all converge
  through exactly Live `0007`.
- Every new `live_cycle_*` table rejects UPDATE and DELETE, proved per table.
- Every frozen constraint is proved by a direct conflicting insert: the snapshot
  `UNIQUE(cycle_slot_id)`, the snapshot `UNIQUE(input_snapshot_hash)`, the pair/
  position/open-order ordinal primary keys and their per-snapshot uniqueness, the
  attempt-terminal `UNIQUE(cycle_attempt_start_id)`, the work-result
  `UNIQUE(cycle_slot_id, work_kind, work_root_key)`, the emergency decision
  `UNIQUE(cycle_slot_id)`, the emergency intent
  `UNIQUE(emergency_risk_decision_id, paper_position_id)` and unique idempotency key,
  the entry Risk `UNIQUE(portfolio_decision_id)`, the approved intent
  `UNIQUE(risk_decision_id)` and unique idempotency key, and the completion
  `UNIQUE(cycle_slot_id)`.
- Both new triggers are proved by direct insert in every branch: the one-incomplete-
  slot trigger (same account with and without a completion, different account) and the
  emergency/ordinary-close exclusion trigger (both directions, and the allowed
  `INTENT_DISPATCH`/`PAPER_COMPLETION` kinds in either mode).
- `INSERT OR IGNORE` is never accepted as proof of equality; every reused row is
  re-read and compared field by field, proved by a fixture whose row differs in one
  column.
- Injected failure at each of the policy, slot, snapshot root, pair lineage, position
  lineage, open-order lineage, attempt start, attempt terminal, work result, emergency
  decision, emergency intent, entry Portfolio, entry Risk, approved intent, and
  completion write boundaries rolls back that entire transaction and leaves zero rows
  from it.
- Identical concurrent writers converge on one insert plus one exact reuse for the
  slot, the snapshot, the work result, and the entry chain; two concurrent freezes of
  one Slot produce exactly one snapshot and one loser that fails closed.
- No numeric column in any new table is SQLite `REAL`, proved by reading
  `PRAGMA table_info` for every new table.

## Composition, CLI, and scope evidence

- `run_once` reads the injected `Clock` exactly once, proved by a counting `Clock`
  double; every additional read observed in the process comes from
  `PaperApplicationService`, which is separately asserted to read it once per intent
  call.
- `run_once` contains no loop that forces order completion, no `sleep`, no backoff, and
  no automatic retry; a source scan of `cycle/` finds no `time.sleep`,
  `time.monotonic`, `datetime.now`, `datetime.utcnow`, `while True`, or retry
  decorator.
- `python -m swap_bot paper-once` requires `--live-database`, `--signal-database`,
  `--cycle-policy`, `--strategy-config`, `--authority`, `--scheduled-for`, `--as-of`,
  and `--worker-identity`; omitting any one fails, and no default value, fixture
  value, or Research default is substituted for any of them.
- The CLI performs exactly one `run_once` call, proved by a counting probe, and prints
  one JSON summary containing the Slot ID, snapshot ID, attempt start ID, outcome, and
  work-result codes.
- A naive or non-UTC `--scheduled-for` or `--as-of` fails before any store is
  constructed.
- The CLI adds no recurring loop, daemon, scheduler, overlap lock, health output,
  metric, alert, burn-in report, or readiness report; a scope test asserts no such
  module or subcommand exists.
- M4 adds no `RuntimeMode.PAPER`, no `LiveArmPolicy` change, no real Broker adapter,
  no multi-account support, no dynamic sizing, and no multi-strategy allocation.
- `apps/swap_bot/src/swap_bot/paper/`, `models.py`, `portfolio.py`, `risk.py`,
  `ports.py`, `execution.py`, `strategy/`, `production_entry.py`,
  `production_strategy_store.py`, `ordinary_close_store.py`, and
  `ordinary_close_application.py` are unchanged by M4, except that
  `adoption_gate.py`, `adoption_store.py`, and
  `packages/fx_signal_store/src/fx_signal_store/store.py` gain exactly the additive
  operations this specification names.
- Living architecture, Swap Bot, data/versioning, repository-structure, test-strategy,
  README, and ExecPlan documentation match the implementation at the end of the Phase.

## Coverage policy

- M4 uses the existing coverage infrastructure (`pytest-cov`/`coverage.py`,
  `branch = true`, the four product source roots configured in `pyproject.toml`).
- No global `fail-under` threshold is set in M4.
- Statement and branch reports are produced and their numbers are recorded in the
  `docs/08_TEST_STRATEGY.md` Coverage section alongside the M2-D and M3 baselines, for
  each new `cycle` module and for each modified existing module.
- The gate is not a percentage. The gate is that every safety-critical branch
  enumerated in this document is exercised by a real test: authority routing
  (LIVE/SHADOW/PAPER), the `captured_at` first-write and retry paths, each excluded
  `CycleSlotId` field, the batch-claim shared-checkpoint and rollback paths, the
  read-only hydration paths, each `CycleMarkOutcome` and each mark integrity
  rejection, the fresh-account and established-account reconciliation paths and each
  reconciliation blocking case, the account-snapshot boundary-equality selection and
  its zero/multiple failures, each frozen open-position and nonterminal-order
  derivation, the `used_margin == 0` and both sides of the strict margin comparison,
  the emergency skip of ordinary close and entry, both directions of the exclusion
  trigger, the deterministic position and Pair orders, each pending-exposure
  contributor, each entry precedence branch, each ordinary-close outcome, each
  deterministic identity derivation, each append-or-compare conflict, each of the
  seven Cycle transactions' rollback paths, both new triggers in every branch, each of
  the sixteen crash-injection boundaries, each of the five specially proved recovery
  scenarios, and the CLI argument and authority rejections.
- A numeric threshold is re-evaluated against the combined M2-D/M3/M4 baseline only
  after the M4 final review, and is not part of M4 acceptance.
- No coverage check is registered in `docs/phases/M4.toml`, for the same reason
  recorded in M3: with no `fail-under` the only deterministic argv available would
  always pass while doubling the suite runtime. The obligation above is verified
  during unit and final review.

## Required checks

- `python -m pytest -q` succeeds on Python 3.11 and Python 3.14, with the branch
  coverage report generated.
- `python -m ruff check .` succeeds.
- strict mypy succeeds for `packages/fx_core/src`, `packages/fx_signal_store/src`,
  `apps/fx_research/src`, and `apps/swap_bot/src`.
- `git diff --check` succeeds.
- Repository text remains UTF-8 without BOM and all checks run from the Japanese
  Windows repository path.
- Gate state proves the complete per-unit reviewer history before final review.
- Final review uses only the immutable bundle, frozen files, full diff, test logs and
  hashes, and a unique reviewer nonce.
