# M3 Frozen Acceptance Criteria

Status: frozen acceptance input for the Phase Goal workflow.

M3 is accepted only when all of the following are true.

## Unit gates

- B1, B2, B3, B4, and B5 completed in order.
- Every B unit is approved by a newly created `phase_reviewer` identity.
- Every rejection is corrected and reviewed by another new reviewer identity.
- A new `phase_final_reviewer` approves the complete Phase after full checks.

## Intent separation and authority evidence

- Three explicit entry points exist, one per exact type. Passing an
  `ApprovedCloseIntent` or `ApprovedLiquidationIntent` to the entry entry point,
  an `ApprovedExecutionIntent` to either reduce-only entry point, or any subclass,
  `Mock`, or structurally identical duck type to any of them raises before any store,
  gateway, or market work.
- No M3 type subclasses, wraps as a union, converts between, or can be passed as
  another approved-intent root, and none of the three accepted contracts gains a
  field or changes identity.
- `PaperOrderIntentLineage` retains `intent_kind`, `source_intent_id`,
  `source_intent_idempotency_key`, `source_intent_content_digest`, and
  `paper_position_id` for every kind; two different source intents never produce the
  same `paper_order_id`, and lineage kind/ID/content is recoverable from persisted
  rows alone.
- `ExecutionAuthorityMode.PAPER` creates Paper records; a completed PAPER run
  persists exactly one order, one plan, and the expected Step/resolution rows.
- `SHADOW_NOT_SUBMITTED` returns a typed result and leaves every `live_paper_*` table
  with exactly zero rows, proved by counting rows in every table, not by a summary
  field.
- `LIVE` raises during prevalidation. A probe proves the store, the fill engine, the
  market-observation reader, and the ledger were never called, and every
  `live_paper_*` table remains empty.
- An `ApprovedCloseIntent` whose own `authority` differs from the supplied authority
  fails closed.
- A Paper order, fill, PnL, or accrual never changes `LiveArmPolicy`, arming
  environment state, Adoption `RuntimeMode`, or any existing Live table.

## Broker isolation evidence

- Every file under `apps/swap_bot/src/swap_bot/paper/` imports no `fx_research`,
  `openai`, `execution`, `ports`, `portfolio`, `risk`, `shadow`, or `llm_feature`
  module, and no name `BrokerGateway`, `ExecutionService`, `GmoPrivatePostTransport`,
  `LiveArmPolicy`, or `HttpClient` appears as an imported or referenced symbol.
- Only `paper/store.py` imports `sqlite3` or `live_migrations`.
- A runtime probe patches `GmoPrivatePostTransport.__init__`, `post_once`,
  `ExecutionService.submit`, and `LiveArmPolicy.is_armed`, runs a complete entry,
  ordinary-close, and emergency-liquidation Paper flow, and observes exactly zero
  calls to each.
- `ExecutionService`, `GmoPrivatePostTransport`, `LiveArmPolicy`, `ports.py`, and
  `models.py` are unchanged by M3.

## Time source evidence

- No application-service entry point accepts a `datetime`, an evaluation instant, or
  an audit instant; the three signatures are asserted, so a caller cannot supply or
  influence `evaluated_at`. The B4 store still takes the instant explicitly, and that
  argument is revalidated rather than trusted.
- The application service reads the injected `Clock` exactly once per call, proved by
  a counting `Clock` double, and every row that call writes carries that same instant
  as its first-write audit timestamp.
- No file under `apps/swap_bot/src/swap_bot/paper/` references `datetime.now`,
  `datetime.utcnow`, `time.time`, `time.monotonic`, or any other ambient clock.
- A `Clock` returning a non-`datetime`, a `datetime` subclass, a naive value, or a
  non-UTC offset fails closed before any store call, and every `live_paper_*` table
  stays empty.
- An evaluation instant earlier than `plan.intent_created_at` fails closed inside the
  transaction and writes no Step, attempt, terminal claim, order event, ledger row,
  or reservation row.
- A regressed evaluation instant — one earlier than the greatest instant already
  persisted for that plan across attempt `evaluated_at` values and the audit
  timestamps of its order events, Steps, attempts, claims, selections, no-market
  outcomes, and Fills — fails closed with no rows written, proved by first appending a
  PENDING attempt at a later instant and then replaying at an earlier one.
- The regression guard specifically blocks the manufactured-terminal path: a fresh
  ordinary-close intent evaluated with a far-future `Clock` cannot both be preceded by
  a legitimate earlier evaluation and reach a terminal `NO_MARKET`; a fixture drives
  `Clock` forward past `evaluation_due_at` and proves the terminal claim,
  `no_fill_terminal_order_state` event, and `ReservationReleaseEvidence` appear only
  when the Clock genuinely crossed that boundary and never from a caller argument.
- A market observation whose `received_at` is after the Clock instant is ineligible,
  proving the local-availability clause is a real constraint rather than a vacuous
  comparison against a caller-chosen value.

## Market evidence and selection evidence

- `PaperMarketObservation` rejects non-positive, non-finite, or `bid > ask` prices,
  naive or non-UTC timestamps, `provider_observed_at > received_at`, blank
  source/source version, and comparison-overriding `str` subclasses; its ID is
  deterministic and recomputed on validation.
- An observation whose `received_at` precedes the source intent's `created_at` is
  ineligible and can never produce a Fill, proved for all three intent kinds.
- A Research `ForwardResult` (and any object that is not exactly a
  `PaperMarketObservation`) cannot enter selection; the exact-type check raises.
- The candidate set is exactly the persisted `live_paper_market_observations` rows,
  never the call's argument tuple and never a union. This is proved by the reviewer's
  divergence case: call 1 at Clock `10:00:03` supplies observation A with
  `received_at 10:00:05`, which T0 persists and which is ineligible under the
  local-availability clause, leaving one PENDING attempt; call 2 at Clock `10:00:07`
  supplies only observation B with `received_at 10:00:06`; the Step resolves by
  selecting A, because A is persisted and sorts first. A run that selects B — the
  supplied-tuple reading — fails.
- An observation that was never persisted by T0 is invisible to selection: passing it
  only as a call argument, without T0 persisting it, produces the same outcome as
  passing nothing.
- Two callers replaying the same intent against the same database select the identical
  observation regardless of which observations each call carried.
- Step 0's window starts exactly at `intent_created_at`; Step `n`'s window starts at
  `due(n-1) + step_gap`; every window has `due = start + step_window_duration`; and
  `step_gap > 0` is enforced so windows never overlap.
- Boundary equality is eligible on all four boundaries: `received_at ==
  intent_created_at`, `received_at == window_start`, `received_at == due`, and
  freshness age exactly equal to `maximum_market_age`. One microsecond beyond each is
  ineligible.
- `provider_observed_at` after `received_at`, after the Step due boundary, or older
  than `maximum_market_age` relative to the due boundary is ineligible.
- An observation not yet locally received at `evaluated_at` is ineligible even when
  it lies inside the frozen window.
- With several eligible observations, selection follows `received_at ASC,
  provider_observed_at ASC, market_observation_id ASC` and a fixture with equal
  `received_at`, equal `(received_at, provider_observed_at)`, and mixed whole-second
  and microsecond timestamps proves the SQL ordering and the in-memory ordering
  select the identical observation.
- An observation already selected by an earlier Step of the same plan is excluded by
  the predicate, and a direct insert attempt is additionally rejected by
  `UNIQUE(fill_evaluation_plan_id, market_observation_id)`.
- A later Step may select a different observation inside its own window.

## PENDING and terminal no-market evidence

- No eligible observation with `evaluated_at < due` appends one immutable
  `PENDING_NO_ELIGIBLE_MARKET` attempt, creates no terminal claim, and leaves the
  Step unresolved.
- Multiple PENDING attempts for one Step coexist, are individually immutable, do not
  change Step identity, and no attempt row is ever updated into a selection or
  no-market row.
- `worker_identity` comes only from the application-service constructor: the
  signatures prove no entry point accepts it, and no `paper/` module reads it from a
  module constant, the environment, the process ID, or the host name. Two services
  constructed with different worker identities produce two distinct attempt rows for
  the same Step at the same `evaluated_at`, and a blank worker identity is rejected at
  construction.
- Each `PaperAttemptDiagnosticCode` value is produced by its own case in the frozen
  precedence over the persisted candidate set: no persisted observation for the plan's
  Pair gives `NO_OBSERVATION_FOR_PAIR`, and persisted Pair-matching observations that
  all fail an eligibility clause give `ALL_OBSERVATIONS_INELIGIBLE`. The code is
  derived by the engine from the candidate set and is not a caller argument.
- After one or more PENDING attempts, a pre-due eligible quote still resolves the
  same Step with a market selection.
- No eligible observation with `evaluated_at >= due` first-writes the terminal claim
  with variant `NO_MARKET` and one `PaperNoMarketOutcome` carrying
  `REJECTED_NO_MARKET_EVIDENCE`.
- After the terminal no-market first-write, replaying with a newly supplied eligible
  observation returns the persisted terminal resolution and creates no selection, no
  Fill, and no second resolution.
- Retry after a persisted `MARKET_SELECTED` resolution reuses the exact persisted
  selection even when a strictly better and eligible observation now exists; no
  second search occurs.
- An attempt appended after terminal resolution is rejected.

## Fill computation evidence

- BUY uses `ask` and SELL uses `bid` as the reference price for both Pairs.
- Slippage is adverse: with positive basis points, the BUY fill price is strictly
  greater than `ask` and the SELL fill price is strictly less than `bid`; with zero
  basis points both equal the reference price exactly.
- The fill price equals the frozen formula evaluated in `Decimal`, asserted against
  independently written expected `Decimal` literals, not by re-running the
  implementation formula.
- Every price, quantity, PnL, margin, and accrual value in production code, in
  persisted rows, and in test expectations is `Decimal`; a `float` supplied at any
  boundary is rejected, and no persisted numeric column uses SQLite `REAL`.
- A price or quantity requiring more than the frozen exact-context precision fails
  closed as an arithmetic integrity error instead of silently rounding.
- `FULL_REMAINING` fills the whole remainder and appends `FILLED`;
  `FRACTION_OF_REMAINING` fills exactly `remaining_before * fraction`, appends
  `PARTIALLY_FILLED`, and leaves the exact `Decimal` remainder.
- Original quantity 1000 with a Step-0 Fill of 400 yields `PARTIALLY_FILLED` and
  remaining 600; Step 1 records `remaining_quantity_before == 600` exactly and a Fill
  of 600 yields `FILLED` with the ordered Fill sum exactly 1000.
- Remaining quantity is reconstructed only from the original quantity and the
  persisted ordered Fills; a mutated external quantity, a later capacity snapshot, or
  a changed policy cannot reinterpret a historical Step.
- `fill_quantity <= 0` or `fill_quantity > remaining_quantity_before` creates no Fill
  and fails closed; a direct overfill insert is rejected.
- The two Steps have ordinals exactly `0, 1`; a skipped ordinal, a duplicate ordinal,
  a speculative future Step, a Step after a terminal order state, and a Step beyond
  `maximum_steps` are all rejected.
- A second Fill for one selection and a second selection for one Step are rejected.

## Order lifecycle evidence

- Order state is projected from ordered events only; no order row carries a state
  column and no event row is ever updated.
- Projection rejects a missing ordinal 0, a non-`ACCEPTED` ordinal 0, a gap, a
  duplicate ordinal, and an illegal consecutive pair.
- Every legal transition in the frozen table is exercised, and the following are
  rejected before any event is appended: `ACCEPTED -> FILLED`,
  `ACCEPTED -> PARTIALLY_FILLED`, `ACCEPTED -> REJECTED`,
  `PARTIALLY_FILLED -> REJECTED`, `PARTIALLY_FILLED -> OPEN`, `FILLED -> anything`,
  `CANCELLED -> anything`, `EXPIRED -> anything`, and `REJECTED -> anything`.
- `REJECTED`, `CANCELLED`, and `EXPIRED` are each reachable through the frozen
  `no_fill_terminal_order_state`, and `CANCELLED`/`EXPIRED` are each reachable
  through `incomplete_terminal_order_state` after a positive partial Fill.

## Position, PnL, and account evidence

- A Paper position is created deterministically from the entry intent;
  `paper_position_id` equals `"paper-position-" + digest(entry payload)` and two
  partial entry Fills of the same entry intent accumulate into that one position.
- The `live_paper_positions` row is written by the transaction that writes the first
  `ENTRY` position fill application, immediately before it, and carries
  `paper_account_id`, `entry_paper_order_id`, `pair`, and `position_side` copied from
  the entry order. After one entry Fill the row exists with exactly those values; a
  second entry Fill re-reads and compares it and inserts no second row.
- A `REDUCE_ONLY` application whose position row is absent fails closed as a
  missing-parent integrity error and does not create the row.
- `reconciled_position_ids` and the account rebuild's position input set are non-empty
  after a single entry Fill, so an implementation that never populates
  `live_paper_positions` cannot report `MATCHED` with
  `unrealized_pnl_total`, `gross_exposure`, `used_margin`, and `open_position_count`
  all zero while real open exposure exists.
- Ordinary close and emergency liquidation attach only to the exact persisted Paper
  position for their `position_id`; a mismatched, unknown, or forged
  `paper_position_id` fails closed and writes nothing.
- Ordinary close requires the persisted position side to be the opposite of
  `intent.side`; emergency liquidation derives its order side from the supplied
  `existing_position_side` and fails closed when that argument disagrees with the
  persisted position side.
- A reduce-only intent whose `pair` differs from the persisted position's Pair fails
  closed with no order, plan, Step, position application, ledger entry, snapshot, or
  reservation row written. This is proved for both `ApprovedCloseIntent` and
  `ApprovedLiquidationIntent`, and specifically with two JPY-quoted Pairs
  (`USD_JPY` position, `MXN_JPY` intent) so the settlement-scope check cannot be
  what rejects it; the cross-Pair realized PnL is never computed and never posted.
- A reduce-only intent whose order `paper_account_id` differs from the persisted
  position's `paper_account_id` fails closed with no rows written, proved with two
  bootstraps where the position belongs to the first and the close order claims the
  second; no realized-PnL ledger entry is posted to either account.
- The reduce-only attachment check covers all four dimensions — position row
  existence, Pair, account, and Side — and each one is rejected independently while
  the other three are valid.
- A reduce-only Fill larger than the current open quantity fails closed as a ledger
  integrity error; the position never flips sign, open quantity never becomes
  negative, and the whole transaction rolls back.
- Weighted-average entry price is reconstructed from the complete ordered entry
  applications for both a single-fill and a two-different-price multi-fill position,
  and is unchanged by reduce-only applications.
- An `ENTRY` position fill application on a position that already holds at least one
  `REDUCE_ONLY` application fails closed as a ledger integrity error. This is proved
  with the exact interleaving the rule exists to forbid: entry order with
  `maximum_steps = 2` fills 400 at 100 on Step 0, an ordinary close reduces 200 at
  110, and the entry order's Step 1 fill is then rejected — no Fill, application,
  ledger entry, snapshot, or reservation row is written, the Step's terminal claim is
  not taken, and the average entry price is still 100.
- Over a fully closed position whose weighted-average entry price is exactly
  representable under `paper-quotient-arithmetic-v1`, the ledger is cash-flow exact:
  for a position opened by two entry fills at different prices and closed by two
  reduce-only fills at different prices, `sum(realized_pnl_amount)` equals, to the
  exact `Decimal`, `sum(close price * quantity) - sum(entry price * quantity)` for a
  LONG position and the negation of that for a SHORT position, computed independently
  in the test rather than by re-running the implementation. The final `open_quantity`
  is exactly zero, `unrealized_pnl_total` for that position is exactly zero, and
  `equity` moves by exactly the realized total.
- For a position whose average is not exactly representable — entry fills of 100 at
  100 and 200 at 101, closed in full — `sum(realized_pnl_amount)` equals the value
  obtained by applying `paper-realized-pnl-v1` to the 34-significant-digit half-even
  basis, computed independently in the test, and differs from the naive cash-flow sum
  only by that basis rounding times the total closed quantity. No error beyond the
  frozen quotient rounding is introduced, and the criterion above is not asserted for
  this fixture.
- Realized PnL is proved for a LONG close and a SHORT close, in both profit and loss
  directions, against independently computed `Decimal` expectations.
- Unrealized PnL marks LONG with `bid` and SHORT with `ask`; swapping the mark side
  changes the result, proving the rule is not incidental.
- Gross exposure, used margin, equity, and available margin follow their frozen
  formulas and versions; `used_margin` uses the explicit bootstrap leverage and no
  default leverage exists anywhere.
- Cash, realized PnL, accrued swap, and unrealized PnL are four separate retained
  values; cash equals the bootstrap initial cash after fills, PnL, and accrual, and
  equity is exactly their sum.
- With two open positions on two different Pairs (`USD_JPY` and `MXN_JPY`), a Fill on
  one Pair produces an account snapshot whose `unrealized_pnl_total`,
  `gross_exposure`, `used_margin`, `available_margin`, `equity`, and
  `open_position_count` include both positions; omitting the non-traded Pair changes
  every one of those values, so the test would fail if an aggregate silently covered
  a subset.
- The required coverage set is exactly the Pairs holding a strictly positive open
  position immediately before this transaction's applications, plus the order's Pair
  for a Fill-applying transaction. A mark set that omits a coverage-set Pair, repeats
  a Pair, includes a Pair outside the set, or contains an observation with
  `received_at > evaluated_at` fails closed as an integrity error, writes no snapshot
  and no ledger row, and is never treated as a reduced aggregate.
- A `FULL_REMAINING` ordinary close that drives a position to exactly zero commits
  successfully while supplying a mark for that Pair, because the Pair was open before
  the applications. Omitting that mark fails closed. The closed Pair contributes
  exactly zero to `unrealized_pnl_total` and `gross_exposure` and is excluded from
  `open_position_count`, and the mark is still recorded in the snapshot's mark tuple.
- An entry Fill that opens a brand-new position requires a mark for the order's Pair
  even though no position existed before, and a mark for a Pair that is neither open
  before nor the order's Pair is rejected as an extra Pair.
- The swap-rollover and swap-correction transactions use the no-Fill coverage rule:
  their coverage set is exactly the Pairs open at the start of that transaction and
  includes no order Pair.
- The mark set is bounded in every transaction that writes an account snapshot. A mark
  whose `received_at` is after the bounding instant fails closed with no snapshot and
  no ledger row, proved separately in an entry-point transaction against the
  Clock-sourced `evaluated_at`, in the swap-rollover transaction against its audit
  instant, and in the swap-correction transaction against its audit instant.
- The swap-rollover, swap-correction, and reconciliation audit instants are validated
  as exact UTC-aware `datetime` values no earlier than the greatest instant in the
  frozen non-regression scan set; a naive, non-UTC, `datetime`-subclass, or regressed
  instant fails closed with no rows written.
- Each of the seven frozen scan-set columns is independently falsifiable and is
  proved: a database is arranged so that column alone holds the account's strictly
  greatest instant, and a T5/T6/T7 instant earlier than it is rejected. An
  implementation scanning any six of the seven therefore fails at least one case. An
  instant earlier than a row belonging to a different account is not rejected, proving
  the scan is account-scoped. With an empty scan set, only UTC exactness is required.
- The three application-service entry points accept no `datetime` and forward no
  caller-supplied instant: their signatures are asserted, and a probe proves the only
  instant reaching the store on those paths is the value the injected `Clock` returned
  for that call.
- Exactly three timestamped store operations are reachable without an
  application-service entry point — swap rollover, swap correction, and reconciliation
  — and no intent-driven call is routed through one. `T0` takes no instant at all.
- No `SQLitePaperStore` method obtains an instant internally: every timestamped method
  receives it explicitly and revalidates it, and no `paper/` module references an
  ambient clock.
- The observation selected for the Step's own execution is not implicitly reused as a
  mark: a run supplying a different mark for that Pair produces the values derived
  from the supplied mark, not from the selected observation.
- The account snapshot identity commits to the ordered mark-observation ID tuple;
  changing one mark changes the snapshot ID.
- Snapshot IDs commit to their inputs and every formula/policy version; changing any
  formula version changes the ID.
- A Pair whose quote currency is not JPY, a bootstrap whose settlement currency is
  not JPY, and any attempt to accrue in a non-JPY currency fail closed; no conversion
  function exists in the Paper package.

## Reconciliation evidence

- `PaperReconciliationResult` has the frozen field list including all three compared
  boundaries, the two-value `PaperReconciliationOutcome`, and the four-value
  `PaperReconciledRecordKind`; `MATCHED` holds if and only if both mismatch tuples
  are empty, and the mismatch tuples are sorted and duplicate-free.
- `reconciled_position_ids` is exactly the set of positions whose `paper_account_id`
  column equals the reconciled account, proved with a second account whose positions
  are excluded.
- An untampered account with two positions on two Pairs, at least one closed
  position, at least one swap accrual and one correction, and at least one open
  order reconciles to `MATCHED` with empty mismatch tuples, and exactly one result
  row is committed.
- Reconciliation rebuilds and compares all four record kinds. Tampering each kind
  independently produces `MISMATCHED` naming exactly that kind and that record ID:
  a `PaperPositionFillApplication` `open_quantity_after` and, separately, its
  `realized_pnl_amount`; a `PaperLedgerEntry` amount; and each retained
  `PaperPositionSnapshot` field.
- Every retained `PaperPositionFillApplication` field is regenerated and compared
  individually, and each of these tampered alone produces a
  `POSITION_FILL_APPLICATION` mismatch naming that application ID:
  `paper_position_id`, `paper_order_id`, `application_kind`, `quantity`, `price`,
  `open_quantity_after`, `realized_pnl_amount`, and
  `paper_position_fill_application_id`.
- A Fill from an ordinary-close or emergency-liquidation order persisted as an `ENTRY`
  application is reported as a `POSITION_FILL_APPLICATION` mismatch because the kind
  disagrees with the owning order's `intent_lineage.intent_kind`, even though
  `open_quantity_after` recomputed from the persisted wrong kind is self-consistent,
  `realized_pnl_amount` is `None`, and no `REALIZED_PNL` ledger entry exists for
  rebuild 2 to compare.
- An `ENTRY` application persisted after a `REDUCE_ONLY` application of the same
  position is reported as a `POSITION_FILL_APPLICATION` mismatch by the rebuild, so
  the ordering rule is enforced on the read path as well as the write path.
- Every retained `PaperAccountSnapshot` aggregate is tampered individually and each
  one alone produces `MISMATCHED`: `cash`, `realized_pnl_total`,
  `unrealized_pnl_total`, `accrued_swap_total`, `equity`, `used_margin`,
  `available_margin`, `gross_exposure`, `open_position_count`, `open_order_count`,
  the mark-observation tuple, and each of `highest_application_seq`,
  `highest_ledger_entry_seq`, and `highest_order_event_seq`. A rebuild that
  regenerates only the ledger-derived fields therefore fails this criterion.
- `open_position_count` and `open_order_count` are proved at their boundaries: an
  order that is still `ACCEPTED`, one that is `OPEN`, and one that is
  `PARTIALLY_FILLED` each count as open, while `FILLED`, `CANCELLED`, `EXPIRED`, and
  `REJECTED` do not; a snapshot taken before a terminal event still counts that order
  as open when rebuilt at its recorded `highest_order_event_seq`.
- An order created after a snapshot, whose every order event has
  `order_event_seq > snapshot.highest_order_event_seq`, is excluded from that
  snapshot's `open_order_count` and its empty truncated event set is never passed to
  `project_paper_order_state`. Reconciling that older snapshot returns a typed
  `MATCHED` result rather than raising a projection integrity error.
- Rebuild 2 requires every ledger entry's `paper_account_id` to equal its position's
  `paper_account_id` read from `live_paper_positions`; an entry posted to a different
  account is reported as a `LEDGER_ENTRY` mismatch by both accounts' reconciliations
  rather than being silently excluded by one and matched by the other.
- A `PaperPositionSnapshot`'s `accrued_swap_total` is rebuilt from that position's
  swap ledger entries up to its recorded `highest_ledger_entry_seq`; adding a later
  accrual does not change the rebuild of an earlier snapshot.
- The account-snapshot rebuild uses the marks named by the persisted snapshot's
  recorded mark-observation tuple, not a freshly supplied mark set; substituting a
  different mark set at reconciliation time does not change the outcome.
- `MISMATCHED` performs no UPDATE, no DELETE, and no repair; the tampered row is left
  as found and one result row is still committed.

## Swap accrual evidence

- An accrual for an open LONG and an open SHORT position produces the frozen formula
  result, one `PaperSwapAccrual`, and exactly one `SWAP_ACCRUAL` ledger entry.
- Each of the seven non-accrual outcomes is produced by its own case: missing
  evidence, Pair mismatch, non-`AVAILABLE` availability, non-JPY settlement currency,
  unsupported `unit_basis`, stale/out-of-window evidence, and a closed position. Each
  writes one typed `PaperSwapNonAccrual`, writes no `PaperSwapAccrual`, and writes no
  ledger entry; a zero-amount accrual is never produced.
- The frozen precedence order is proved by at least one case where two non-accrual
  conditions hold simultaneously.
- Swap effective-window endpoints and `maximum_swap_age` equality remain eligible.
- `rollover_date` is an exact `datetime.date`; a `datetime`, a string, or a `date`
  subclass is rejected. `rollover_at` equals the UTC midnight beginning that date,
  proved at a date boundary where the evidence window makes the previous and next
  UTC day differ in outcome.
- Evidence with `effective_until is None` accrues whenever
  `rollover_at >= effective_from` and is not otherwise stale; the absent upper bound
  is not treated as an immediate expiry and not as an unbounded bypass of the
  remaining staleness rules.
- Evidence whose `received_at` is after `rollover_at` yields
  `NOT_ACCRUED_SWAP_STALE`, proving swap accrual has no lookahead.
- `unit_basis` conversion uses only the explicit policy mapping; an unmapped basis is
  never assumed and never defaulted.
- One `(paper_position_id, rollover_date)` admits at most one accrual or non-accrual
  across both variants, proved at the schema boundary: `live_paper_swap_rollover_claims`
  has exactly the five frozen columns, `evidence_id` holds the variant child record ID
  (`paper_swap_accrual_id` for `ACCRUED`, `paper_swap_non_accrual_id` for
  `NOT_ACCRUED`), and the CHECK rejects `evidence_id = paper_position_id`.
- The two rollover child-linkage triggers are proved: a direct insert of a
  `PaperSwapAccrual` for a date already claimed `NOT_ACCRUED` is rejected, as is the
  reverse; and an accrual or non-accrual inserted with no claim, or with a claim whose
  `evidence_id` names a different record, is rejected. Reusing one `evidence_id` across
  two claims is rejected by `UNIQUE(evidence_id)`.
- The swap-rollover and swap-correction transactions require every mark to already
  exist in `live_paper_market_observations`; neither writes a market observation, and a
  mark with no persisted row is a missing-parent integrity failure that rolls the
  transaction back with no claim, accrual, non-accrual, correction, ledger entry, or
  snapshot written.
- A correction appends `PaperSwapAccrualCorrection` plus one delta
  `SWAP_ACCRUAL_CORRECTION` ledger entry; the original accrual row, its ledger entry,
  and the rollover claim are unchanged, and no UPDATE occurs.
- Two sequential corrections of one accrual converge to the last replacement amount:
  an accrual of 100 corrected to 120 and then to 130 produces three ledger entries of
  exactly 100, 20, and 10 and an `accrued_swap_total` of exactly 130 — never 150.
  Each correction records its `chain_ordinal`, `predecessor_correction_id`,
  `effective_amount_before`, `replacement_amount`, and `delta_amount`.
- An oscillating chain converges too: an accrual of 100 corrected to 120, then back to
  100, then again to 120 writes three distinct correction rows with `chain_ordinal`
  `1, 2, 3`, four ledger entries of exactly 100, 20, -20, and 20, and an
  `accrued_swap_total` of exactly 120. The third correction is not byte-identical to
  the first, does not collide on `correction_id`, is not silently reused by
  append-or-compare, and is not blocked by
  `UNIQUE(entry_kind, source_evidence_id)`.
- Chain integrity fails closed: a `chain_ordinal` that is not exactly
  `len(existing chain) + 1`, a `predecessor_correction_id` that is not the chain's
  current last correction, a `None` predecessor at an ordinal above `1`, a non-`None`
  predecessor at ordinal `1`, and an `effective_amount_before` or `delta_amount` that
  disagrees with the recomputed value are each rejected with no rows written.
- The correction transaction persists a fresh `PaperPositionSnapshot` and
  `PaperAccountSnapshot` at the new boundaries, so `accrued_swap_total` and `equity`
  reflecting the corrected amount are read from persisted rows rather than computed
  ad hoc in a test, and reconciling those snapshots reports `MATCHED`.
- A non-accrual writes no snapshot, and an accrual writes exactly one new position
  snapshot and one new account snapshot.
- `PaperSwapAccrual` binds the exact caller-supplied `paper_position_snapshot_id`. A
  snapshot ID belonging to another position, one whose `open_quantity` differs from
  the accrual quantity, and one superseded by a later application or later swap ledger
  entry are each rejected with no rows written. The accrual never binds a snapshot
  written by its own transaction, and no accrual code path uses `ORDER BY ... DESC`,
  `LIMIT 1`, or any other latest-row resolution.
- `OperationalSwapEvidence` and its store are unchanged by M3.

## Reservation settlement evidence

- An ordinary-close partial Fill appends exactly one consumption of exactly the
  filled quantity in the same transaction as the Fill, and the unfilled remainder is
  not released.
- The conservation equation `consumed + outstanding + released == intent.quantity`
  holds after: no fill, one partial fill, two partial fills, a full fill, and a
  terminal release after a partial fill.
- A terminal `CANCELLED`, `EXPIRED`, and `REJECTED` order each appends exactly one
  release of exactly the remainder, in the same transaction as the terminal event.
- Exhausting `maximum_steps` with a positive remainder in one transaction follows the
  T3b branch: with `maximum_steps = 1`, `FRACTION_OF_REMAINING` fraction `0.4`, and a
  close intent of 1000, Step 0 writes exactly one consumption of 400 and exactly one
  release of exactly 600, appends the ordered order events `PARTIALLY_FILLED` then
  `incomplete_terminal_order_state` at consecutive ordinals, and satisfies
  `consumed + outstanding + released == 1000` with `outstanding == 0`. Neither
  leaving 600 permanently reserved nor releasing 1000 is accepted.
- `consumed_total` is evaluated inside the writing transaction and includes the
  consumption written by that same transaction: the T3b release equals
  `intent.quantity - consumed_total` computed after the consumption row exists, and
  an implementation reading only previously committed rows produces
  `consumed + released > intent.quantity` and fails this criterion.
- Terminal `FILLED` appends no release. A process crash, a bare retry, a PENDING
  attempt, and a new M2-D capacity observation each append no release.
- A second consumption for one Fill, a second release for one order, a consumption
  after a release, a release whose quantity is not the exact remainder, and a
  consumption that would exceed the intent quantity are each rejected with no partial
  rows.
- Entry and emergency-liquidation orders create zero consumption and zero release
  rows.
- The consumption/release join key is the exact `ApprovedCloseIntent.idempotency_key`
  used by M2-D reservation snapshots, proved by reading an M2-D-persisted Intent and
  settling against it; no M2-D table or M2-D module changes.

## Persistence, recovery, and migration evidence

- Live migration `0006_paper_execution_ledger.sql` is additive and does not alter
  migrations `0001` through `0005` or any Signal Store migration; the architecture
  test asserts the exact six-file migration set.
- Fresh database, upgrade from a database at `0005`, reopen/rerun, migration
  body/marker failure rollback and retry, and concurrent initialization all converge
  through exactly Live `0006`.
- Every M3 table rejects UPDATE and DELETE, proved per table.
- The frozen constraint set is proved by direct conflicting inserts: a second plan
  for one order, a second Step for one `(plan_id, ordinal)`, a second terminal claim
  for one Step across both variants, a second selection for one Step, and a second
  Fill for one selection.
- A `MARKET_SELECTED` claim blocks a later `NO_MARKET` resolution for the same Step
  and vice versa, proving the claim is cross-variant and not merely per-variant.
- `live_paper_step_terminal_claims` has exactly the four frozen columns, and
  `resolution_id` holds the variant child record ID: the
  `market_observation_selection_id` for `MARKET_SELECTED` and the
  `no_market_outcome_id` for `NO_MARKET`. A claim whose `resolution_id` equals its
  `fill_evaluation_step_id` is rejected by the CHECK, so the uniqueness cannot be made
  vacuous.
- The two child-linkage triggers are proved: inserting a selection with no claim,
  with a `NO_MARKET` claim, or with a claim whose `resolution_id` is a different
  selection ID is rejected; the symmetric three cases are rejected for a no-market
  outcome. Reusing one `resolution_id` across two Steps is rejected by
  `UNIQUE(resolution_id)`.
- `live_paper_order_events` assigns a monotonic `order_event_seq` and enforces
  `UNIQUE(paper_order_id, event_ordinal)`; `paper_account_id` columns exist on orders,
  positions, ledger entries, and both snapshot kinds, and the account-to-position and
  account-to-order relations are read from those columns rather than inferred.
- Exact replay of a completed intent returns the persisted order, plan, Step,
  resolution, selection, and Fill and adds zero semantic rows; only additional
  PENDING attempts may legitimately accumulate, and only before terminal resolution.
- A conflicting second write with the same content-addressed ID but different content
  fails closed without partial rows for the order, plan, Step, claim, selection,
  Fill, ledger entry, snapshot, accrual, consumption, and release.
- A missing or corrupted parent (order, plan, Step, selection, position, bootstrap,
  fill policy, market observation) is an integrity error, is never repaired, is never
  reclassified as a business outcome, and never becomes a reused row.
- `INSERT OR IGNORE` is never accepted as proof of equality; every reused row is
  re-read and compared field by field.
- Injected failure at each of the order, plan, Step, terminal claim, selection, Fill,
  position application, ledger entry, position snapshot, account snapshot, rollover
  claim, accrual, non-accrual, correction, consumption, release, and reconciliation
  result write boundaries rolls back that entire transaction and leaves zero rows
  from it.
- Restart after each of those boundaries converges to one logical result with no
  duplicate order, Step ordinal, resolution, selection, Fill, ledger entry, snapshot,
  or accrual, and remaining quantity is reconstructed from persisted ordered Steps
  and Fills.
- Identical concurrent writers converge on one insert plus one exact reuse for the
  order, the plan, and the Step; distinct concurrent resolutions of one Step yield
  exactly one terminal claim and one loser that fails closed.
- No new rows appear in legacy entry Candidate/Portfolio/Risk/Execution tables or in
  any M2-D table.

## Composition and scope evidence

- One call processes exactly one intent once: it never loops to force completion,
  never sleeps, never backs off, and never retries automatically. An unresolved Step
  returns `PAPER_STEP_PENDING` as a typed result.
- Manual exact replay adds no semantic records and returns the same typed result
  content.
- No `CycleSlot`, `CycleInputSnapshot`, `CycleAttempt`, recurring cycle, scheduler,
  daemon, overlap lock, CLI command, Position discovery, Pair batch selection,
  latest-row selection, real Broker adapter, `LIVE` execution path, canary rollout,
  dynamic swap provider, multi-currency accounting, or backtest framework is added.
- Living architecture, Swap Bot, data/versioning, repository-structure,
  test-strategy, README, and ExecPlan documentation match the implementation at the
  end of the Phase.

## Coverage policy

- M3 uses the existing coverage infrastructure (`pytest-cov`/`coverage.py`,
  `branch = true`, the four product source roots configured in `pyproject.toml`).
- No global `fail-under` threshold is set in M3.
- Statement and branch reports are produced and their numbers are recorded in the
  `docs/08_TEST_STRATEGY.md` Coverage section alongside the 2026-08-07 M2-D baseline,
  for each new Paper module.
- The gate is not a percentage. The gate is that every safety-critical branch
  enumerated in this document is exercised by a real test: authority routing
  (PAPER/SHADOW/LIVE), each Clock validation and the per-plan monotonicity guard,
  each eligibility clause and each boundary equality, PENDING versus terminal
  no-market, the persisted-candidate-set divergence case, each legal and each illegal
  order transition, each partial-fill mode,
  each of the three T3 branches, each overfill and duplicate rejection, each of the
  four reduce-only attachment rejections (position row, Pair, account, Side), the
  position-row creation and missing-position-row paths, the entry-after-reduce-only
  rejection and the closed round-trip cash-flow identity, each retained position fill
  application field regenerated and tampered, each PnL direction and mark
  side, each mark-set coverage failure including the full-close case, the mark
  bounding-instant rejection in all three snapshot-writing transaction families, the
  no-events-at-boundary `open_order_count` exclusion, each attempt diagnostic code,
  each of the eight swap accrual outcomes, the
  chained and oscillating correction delta paths and each chain-integrity rejection,
  the accrual snapshot-binding rejections, both rollover child-linkage triggers, the
  T5/T6 missing-mark-parent rejection, each of the seven scan-set columns, each of the
  four reconciled record kinds
  with every retained account-snapshot aggregate tampered individually, each claim
  child-linkage trigger, each reservation consume/release authorisation and
  rejection, each corruption and missing-parent path, and each crash-injection
  boundary.
- A numeric threshold is re-evaluated against the combined M2-D/M3 baseline only
  after the M3 final review, and is not part of M3 acceptance.
- No coverage check is registered in `docs/phases/M3.toml`: with no `fail-under` the
  only deterministic argv available would always pass while doubling the suite
  runtime, and it would add an optional-plugin dependency to the gate. The obligation
  above is therefore verified during unit and final review.

## Required checks

- `python -m pytest -q` succeeds.
- `python -m ruff check .` succeeds.
- strict mypy succeeds for `packages/fx_core/src`, `packages/fx_signal_store/src`,
  `apps/fx_research/src`, and `apps/swap_bot/src`.
- `git diff --check` succeeds.
- Repository text remains UTF-8 without BOM and all checks run from the Japanese
  Windows repository path.
- Gate state proves the complete per-unit reviewer history before final review.
- Final review uses only the immutable bundle, frozen files, full diff, test logs and
  hashes, and a unique reviewer nonce.
