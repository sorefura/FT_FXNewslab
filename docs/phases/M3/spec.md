# M3 Frozen Specification — Paper Execution and Ledger

Status: frozen design input for the Phase Goal workflow.

This snapshot defines Milestone 3 only. The living ExecPlan remains the place for
progress and decision-log updates; implementation must not edit this file after the
phase baseline is committed.

## Objective

Turn one exact approved intent into a deterministic Paper Order and Paper Fill that
never reaches a real Broker, and reflect that Fill into an append-only Paper
position / account / PnL / swap ledger:

```text
exact approved intent (one of three exact roots)
-> PaperOrder + ordered immutable order events
-> exactly one FillEvaluationPlan
-> ordered FillEvaluationStep (ordinal 0..)
-> PENDING attempts or one terminal Step resolution
-> zero or one MarketObservationSelection -> zero or one PaperFill
-> atomic Paper ledger application (position, realized PnL, account)
-> typed reservation consumption or release for ordinary close
```

After restart, manual retry, or crash recovery no semantic duplicate is created and
the same state is reconstructible from persisted evidence.

## Non-goals

- `CycleSlot`/`CycleInputSnapshot` implementation, `CycleAttempt`, recurring cycle,
  scheduler, daemon, overlap lock, CLI, operational restart composition, or burn-in
  reporting. M4 owns all of these.
- Position discovery, Pair batch selection, latest-row selection, or any implicit
  "current" record lookup.
- Real Broker adapter, `BrokerGateway` implementation, GMO Private POST, `LIVE`
  execution, canary rollout, or any `LiveArmPolicy` change.
- Changes to `ExecutionService`, `GmoPrivatePostTransport`, `ports.BrokerGateway`,
  `models.py` accepted contracts, Strategy, M2-B materialization, Adoption, or M2-D
  ordinary-close evaluation/Portfolio/Risk semantics.
- Mandatory external dynamic Swap provider or any new `SwapDataSource` adapter.
- Generic multi-currency accounting, FX conversion, optimization, or backtesting.
- Cancellation triggered by an operator, scheduler, or external command. M3 reaches
  `CANCELLED`/`EXPIRED`/`REJECTED` only through the frozen no-fill terminal policy.
- Randomised fill models, seeds, sleep, backoff, or automatic retry.

## Cross-unit invariants

- Only `ExecutionAuthorityMode.PAPER` may create a PaperOrder, order event, plan,
  Step, attempt, resolution, selection, Fill, ledger record, snapshot, accrual, or
  reservation evidence.
- `SHADOW_NOT_SUBMITTED` creates zero Paper records of every kind and returns a typed
  result. `LIVE` fails during entry-point prevalidation, before any Paper gateway,
  store, market-observation read, or market selection.
- Paper infrastructure never imports, constructs, or calls `BrokerGateway`,
  `ExecutionService`, `GmoPrivatePostTransport`, `LiveArmPolicy`, GMO Private POST,
  any other real Broker transport, or any `fx_research` module.
- Paper success, PnL, accrual, or burn-in never creates Live authority and never
  changes Adoption state.
- The three approved-intent roots keep their accepted identities. There is no
  inheritance, union type, action string, or conversion type between them. The public
  API has one explicit entry point per exact type and each entry point requires
  `type(intent) is <exact class>`.
- All money, quantity, price, PnL, margin, and accrual values are `Decimal`. Binary
  float is forbidden in production code, in persistence, and in test expectations.
  SQLite `REAL` is never used for a semantic value.
- Every semantic identity is content-addressed with the existing canonical
  SHA-256 helper (`swap_bot.adoption.digest`). Python `hash()` is forbidden. First-
  write audit timestamps (`created_at`, `selected_at`, `resolved_at`, `appended_at`,
  `applied_at`) are excluded from every semantic identity.
- Every external/store boundary requires exact concrete runtime types, calls the base
  `__post_init__`/`validate_intrinsic_integrity` implementation directly, and rejects
  comparison-overriding `str` subclasses (`type(value) is str`) before any semantic
  equality test.
- A content-addressed ID alone never authorizes anything. Every persistence boundary
  rehydrates the exact parent record and compares full content before reuse.
- Persistence is append-or-compare. `INSERT OR IGNORE` alone is never proof of
  equality; the row is re-read and compared field by field. Every M3 table has
  UPDATE and DELETE rejection triggers.
- Persisted corruption, a missing parent, or a conflicting second write is an
  integrity failure. It is never a business outcome, never repaired, never retried
  automatically, and leaves no partial rows.
- Retry reuses the first successful semantic evidence exactly. Remaining quantity,
  Step windows, due boundaries, policy versions, resolutions, selections, and Fills
  are never recomputed from current wall-clock time or from any mutable external
  state; they are reconstructed from persisted ordered Steps and Fills.
- Settlement scope is JPY only (see "Frozen accounting model").
- No application-service entry point accepts, forwards, or defaults an evaluation
  instant or audit timestamp; the only instant an entry point uses is the one it reads
  from the injected `Clock`. Every B4 store method that writes a timestamped row takes
  its instant as an explicit argument and revalidates it in-transaction; no store
  method obtains an instant internally. No module under `paper/` calls
  `datetime.now()`, `datetime.utcnow()`, `time.time()`, or any other ambient clock.

## Frozen time source

`evaluated_at` is the sole authority for the local-availability eligibility clause and
for first-writing a terminal `NO_MARKET` claim, so it is a controlled external
boundary, never a free parameter.

- B5 defines one `Clock` Protocol with a single method `now() -> datetime`, injected
  into the application service constructor. It is the only time source on the
  application-service surface and the only time source M3 composes; M3 contains no
  ambient clock anywhere. It is the replaceable external boundary AGENTS.md requires.
- No application-service entry point accepts an evaluation instant, an audit instant,
  or any other `datetime` argument. A caller cannot supply, override, or influence
  `evaluated_at`; only a substituted `Clock` implementation can, and substituting one
  is an explicit composition decision, not an argument of a business call.
- The application service reads the `Clock` exactly once per call. That single instant
  is `evaluated_at`, and it is also the first-write audit instant for every row that
  call writes (`created_at`, `selected_at`, `resolved_at`, `appended_at`,
  `applied_at`). A call therefore has exactly one time value.
- The returned value must be an exact `datetime` and UTC-aware. A non-`datetime`, a
  `datetime` subclass, a naive value, or a non-UTC offset fails closed before any
  store call.
- B4 revalidates the supplied instant inside the same `BEGIN IMMEDIATE` transaction,
  before any Step work, terminal claim, order event, ledger row, or reservation row,
  against persisted state:
  - `evaluated_at >= plan.intent_created_at`;
  - `evaluated_at >=` the greatest instant already persisted for that plan across
    every attempt `evaluated_at` and every audit timestamp of its order events,
    Steps, attempts, claims, selections, no-market outcomes, and Fills, so the
    evaluation instant is non-decreasing per plan.
  A violation is an integrity failure that rolls the transaction back and writes
  nothing.
- Because `evaluated_at` is Clock-sourced, monotonic per plan, and never before the
  intent, the eligibility clause `received_at <= evaluated_at` is a real
  local-availability constraint, and a terminal `NO_MARKET` claim can only be reached
  by a Clock that has genuinely passed the Step due boundary.
- The store takes the instant as an explicit argument so B4 remains testable without
  a Clock; B4 adds no Clock, and B1/B2/B3 remain pure and take `evaluated_at` as a
  supplied value.
- **What actually distinguishes T5, T6, and T7.** Every store method that writes a
  timestamped row takes an explicit instant, so "takes an instant" separates nothing.
  What is frozen is where the instant comes from:
  - T1 through T4 are reachable only from an application-service entry point, and that
    entry point passes the single `Clock` instant it read for that call. No other
    caller path into them exists in M3.
  - T5, T6, and T7 are driven by no approved intent and have no entry point, so their
    caller supplies the instant directly; M4 owns sourcing it from the same `Clock`.
    M3 adds no fourth entry-point-free timestamped operation, and no intent-driven
    call is routed through one.
  - T0 writes only `PaperMarketObservation` rows, which carry their own provider and
    receipt times and no separate audit column, so it takes no instant at all. It is a
    public store method the application service calls and a caller may also call
    directly.
  - In every case B4 revalidates the supplied instant inside the transaction as an
    exact `datetime`, UTC-aware, and within the bounds frozen here; no store method
    ever obtains an instant internally.
- **Frozen T5/T6/T7 non-regression scan set.** Because every row a transaction writes
  carries that transaction's single instant, the scan set is one column per
  transaction that can be the account's latest writer, chosen so each entry can be the
  sole greatest instant and is therefore independently falsifiable. For the target
  `paper_account_id`, take the greatest instant across exactly these seven columns:
  - `order_events.appended_at` for that account's orders — covers T1 and T3;
  - `fill_evaluation_steps.created_at` for the plans of that account's orders —
    covers T2, whose ordinal-`n>0` case writes no order event;
  - `fill_evaluation_attempts.evaluated_at` for those plans — covers T4;
  - `swap_accruals.created_at` and `swap_non_accruals.created_at` for that account's
    positions — cover the two T5 branches;
  - `swap_accrual_corrections.created_at` for those positions — covers T6; and
  - `reconciliation_results.created_at` for that account — covers T7.

  Every other timestamped row is co-written with one of these at the identical
  instant, so scanning it would add no bound. An empty scan set imposes no lower bound
  beyond UTC exactness. This list is exact: scanning fewer columns, or scanning rows
  of another account, is not a valid implementation.

## Frozen Decimal arithmetic

Two explicit, versioned arithmetic modes. Every M3 numeric formula names exactly one
of them, and every computation runs inside `decimal.localcontext()` with that context.

- `paper-exact-arithmetic-v1`:
  `Context(prec=50, rounding=ROUND_HALF_EVEN, traps=[InvalidOperation,
  DivisionByZero, Overflow, Inexact])`. Used for every addition, subtraction,
  multiplication, and comparison: slippage adjustment, fill notional, realized PnL,
  unrealized PnL, gross exposure, equity, available margin, swap accrual amount, and
  every quantity sum. Because `Inexact` is trapped, a value that cannot be
  represented exactly in 50 significant digits raises and the operation fails closed
  as an arithmetic integrity error; it is never silently rounded.
- `paper-quotient-arithmetic-v1`:
  `Context(prec=34, rounding=ROUND_HALF_EVEN, traps=[InvalidOperation,
  DivisionByZero, Overflow])`. Used only by the two division-bearing formulas:
  weighted-average entry price and used margin, plus the swap quantity ratio. Results
  are deterministic 34-significant-digit half-even values.

No `Decimal.quantize()`, no lot-size rounding, no minimum order size, and no
decimal-place normalisation exists anywhere in M3.

## Frozen Paper evidence model

### Intent kind, lineage, and Paper position identity

`PaperIntentKind` is the discriminator, a `StrEnum` with exactly:

```text
ENTRY | ORDINARY_CLOSE | EMERGENCY_LIQUIDATION
```

Each kind is produced only by its own entry point from its own exact source type:
`ApprovedExecutionIntent`, `ApprovedCloseIntent`, `ApprovedLiquidationIntent`.

The frozen source-intent identity payloads are the complete field set of each
accepted root, in this exact order, with `Decimal` as `str(...)`, `datetime` as
`.isoformat()`, `CurrencyPair` as `.symbol`, enums as `.value`, and typed IDs as
`.value`:

- ENTRY: `intent_id`, `candidate_id`, `risk_decision_id`, `pair`, `side`,
  `quantity`, `idempotency_key`, `created_at`.
- ORDINARY_CLOSE: `close_candidate_id`, `portfolio_decision_id`, `risk_decision_id`,
  `capacity_evidence_id`, `position_id`, `pair`, `side`, `quantity`, `authority`,
  `idempotency_key`, `created_at`.
- EMERGENCY_LIQUIDATION: `intent_id`, `risk_decision_id`, `position_id`, `pair`,
  `quantity`, `idempotency_key`, `created_at`.

`PaperOrderIntentLineage` is the only normalisation surface. Its exact fields are:

- `intent_kind: PaperIntentKind`;
- `source_intent_id: str` — ENTRY and EMERGENCY_LIQUIDATION use `intent_id.value`;
  ORDINARY_CLOSE uses `idempotency_key` (its only intrinsic identity);
- `source_intent_idempotency_key: str` — the source `idempotency_key` for all kinds;
- `source_intent_content_digest: str` — `digest(<the payload above for that kind>)`;
- `paper_position_id: str`.

Paper position identity is derived from the entry intent alone:

```text
paper_position_id = "paper-position-" + digest(ENTRY source-intent payload)
```

For ENTRY, `paper_position_id` must equal
`"paper-position-" + source_intent_content_digest`. A Paper position's business
identity is exactly `PositionId(paper_position_id)`. For ORDINARY_CLOSE and
EMERGENCY_LIQUIDATION, `paper_position_id` is `intent.position_id.value`; B4
authenticates it against the persisted Paper position row and its originating entry
lineage. Reduce-only attachment therefore always resolves to one exact Paper
position created by one exact entry intent.

`ApprovedCloseIntent` is the only root with a persisted parent row in this
repository, so it is the only root that B4 rehydrates and compares (see "Frozen
reservation settlement model"). `ApprovedExecutionIntent` and
`ApprovedLiquidationIntent` have no persisted Live row at this baseline; the supplied
exact object is validated intrinsically and then bound immutably into the order
lineage. M3 adds no store for them and writes to no legacy entry table.

`ApprovedLiquidationIntent` carries no `Side`. Its entry point therefore requires one
additional exact keyword argument `existing_position_side: Side`; the Paper order
side is the opposite Side, and B4 requires the argument to equal the persisted Paper
position's side. `ApprovedCloseIntent` already carries the close `side`, so its entry
point takes no extra argument and B4 requires the persisted position side to be the
opposite of `intent.side`. No accepted contract gains a field.

**Frozen reduce-only attachment predicate.** Neither `ApprovedExecutionIntent` nor
`ApprovedLiquidationIntent` has a persisted Live row, and `models.py` validates only
quantity, idempotency key, and UTC `created_at`, so nothing in an accepted contract
binds its `pair` or its account to its `position_id`. B4 therefore authenticates all
four dimensions against the hydrated `live_paper_positions` row before writing
anything, for both `ORDINARY_CLOSE` and `EMERGENCY_LIQUIDATION`:

1. the row exists for `paper_position_id`;
2. `intent.pair == position.pair` — a reduce-only order can only ever reduce a
   position in its own Pair, and a Pair mismatch is an integrity failure even when
   both Pairs are JPY-quoted and therefore pass the settlement-scope check;
3. `paper_account_id` of the reduce-only order equals `position.paper_account_id`, so
   realized PnL can never be posted to a second account;
4. the Side rule above.

Any mismatch fails closed with no order, plan, Step, application, ledger entry,
snapshot, or reservation row written. Rule 2 is what makes the mark-set coverage proof
("a Fill can only open or reduce the order's own Pair") a consequence of an enforced
rule rather than an assumption; rule 3 is what makes the account-to-position relation
consistent across orders, applications, ledger entries, and both snapshot kinds.

### `PaperMarketObservation`

Immutable, content-addressed, Live-owned. Exact fields:

- `market_observation_id` = `"paper-market-" + digest(identity payload)`;
- `observation_contract_version` = `"paper-market-observation-v1"`;
- `pair: CurrencyPair`;
- `bid: Decimal`, `ask: Decimal`, both positive and finite, `bid <= ask`;
- `provider_observed_at: datetime` UTC;
- `received_at: datetime` UTC, `provider_observed_at <= received_at`;
- `source: str`, `source_version: str`, both non-blank exact `str`.

The identity payload is every field above except the ID, in that order. The
contract imports no `fx_research` type and has no relationship to `ForwardResult`.
Digest output is lowercase hexadecimal, so the observation ID is ASCII and SQLite
`BINARY` ordering equals Python code-point ordering.

### `PaperFillPolicy`

Immutable, content-addressed
(`paper_fill_policy_id = "paper-fill-policy-" + digest(...)`), no hidden default, no
binary float, no randomness. Exact fields:

- `policy_contract_version` = `"paper-fill-policy-v1"`;
- `policy_version: str`;
- `market_selection_policy_version: str`;
- `fill_model_version: str`;
- `step_schedule_policy_version: str`;
- `maximum_market_age: timedelta`, strictly positive (freshness);
- `step_window_duration: timedelta`, strictly positive;
- `step_gap: timedelta`, strictly positive (guarantees non-overlapping windows);
- `maximum_steps: int`, `>= 1`;
- `partial_fill_mode: PaperPartialFillMode` in `{FULL_REMAINING,
  FRACTION_OF_REMAINING}`;
- `partial_fill_fraction: Decimal | None` — required, finite, and in `(0, 1]` iff
  the mode is `FRACTION_OF_REMAINING`; otherwise exactly `None`;
- `slippage_basis_points: Decimal`, finite, `>= 0`;
- `no_fill_terminal_order_state: PaperOrderState` in
  `{REJECTED, CANCELLED, EXPIRED}`;
- `incomplete_terminal_order_state: PaperOrderState` in `{CANCELLED, EXPIRED}`.

M3 v1 defines no randomised fill model, so the policy has no seed root and no Step
has a seed field. Any future randomised model requires a new policy contract version
whose identity includes its seed root; this is a frozen prohibition, not a deferred
field.

### `PaperOrder` and its lifecycle

`PaperOrder` exact fields: `paper_order_id`
(`"paper-order-" + digest(...)`), `order_contract_version` = `"paper-order-v1"`,
`paper_account_id: str`, `intent_lineage: PaperOrderIntentLineage`,
`pair: CurrencyPair`, `side: Side`, `original_quantity: Decimal` (positive finite,
exactly the source intent quantity), `authority: ExecutionAuthorityMode` (must be
`PAPER`), `fill_policy_id: str`, `intent_created_at: datetime`, and audit
`created_at`. `created_at` is excluded from identity. There is no `state` field, no
mutable status column, and no UPDATE path. `paper_account_id` is the account→order
linkage; every Paper position inherits it from the entry order that created it.

`PaperOrderState` is a `StrEnum` with exactly:

```text
ACCEPTED | REJECTED | OPEN | PARTIALLY_FILLED | FILLED | CANCELLED | EXPIRED
```

`PaperOrderEvent` exact fields: `paper_order_event_id`, `paper_order_id`,
`event_ordinal: int`, `state: PaperOrderState`, `source_evidence_kind: str`,
`source_evidence_id: str | None`, and audit `appended_at` (excluded from identity).

**State projection rule.** `project_paper_order_state(events)` requires the events of
one order to have contiguous ordinals starting at `0`, requires ordinal `0` to carry
state `ACCEPTED`, requires every consecutive `(previous, next)` pair to be a legal
transition, and returns the state of the highest ordinal. A gap, duplicate ordinal,
missing ordinal `0`, non-`ACCEPTED` ordinal `0`, or illegal pair raises a projection
integrity error. Current state is never read from a column.

**Legal transition table (exhaustive).**

| from | legal next states |
|---|---|
| (no event) | `ACCEPTED` at ordinal 0 only |
| `ACCEPTED` | `OPEN` |
| `OPEN` | `PARTIALLY_FILLED`, `FILLED`, `CANCELLED`, `EXPIRED`, `REJECTED` |
| `PARTIALLY_FILLED` | `PARTIALLY_FILLED`, `FILLED`, `CANCELLED`, `EXPIRED` |
| `FILLED` | none (terminal) |
| `CANCELLED` | none (terminal) |
| `EXPIRED` | none (terminal) |
| `REJECTED` | none (terminal) |

Every other ordered pair is illegal, including `PARTIALLY_FILLED -> REJECTED`,
`ACCEPTED -> FILLED`, `ACCEPTED -> REJECTED`, and any self-transition other than
`PARTIALLY_FILLED -> PARTIALLY_FILLED`. The transition is validated before the event
row is appended; an illegal transition raises and appends nothing.

**Frozen producers.** `ACCEPTED` is appended with the order and plan. `OPEN` is
appended with Step 0. `PARTIALLY_FILLED` is appended with a Fill leaving a positive
remainder. `FILLED` is appended with a Fill leaving zero remainder.
`no_fill_terminal_order_state` is appended when a Step resolves `NO_MARKET` and the
order has zero persisted Fills. `incomplete_terminal_order_state` is appended when a
positive remainder cannot continue (a `NO_MARKET` resolution after at least one Fill,
or `maximum_steps` exhausted with a positive remainder). No other producer exists.

### Cardinality

```text
Approved Intent
  -> exactly one PaperOrder
    -> ordered immutable PaperOrderEvent, contiguous ordinals from 0
    -> exactly one FillEvaluationPlan
      -> ordered FillEvaluationStep, contiguous ordinals from 0
        -> zero or more FillEvaluationAttempt (all PENDING, immutable)
        -> exactly one terminal StepResolution claim per resolved Step
        -> zero or one MarketObservationSelection
          -> zero or one PaperFill
```

`PENDING` is never a terminal resolution and never consumes the terminal claim.
`PaperStepResolutionVariant` is a `StrEnum` with exactly `MARKET_SELECTED` and
`NO_MARKET`; M3 defines no other variant.

### Plan, Step, attempt, resolution, selection, Fill

`FillEvaluationPlan`: `fill_evaluation_plan_id`, `plan_contract_version`,
`paper_order_id`, `intent_lineage`, `pair`, `side`, `original_quantity`,
`fill_policy_id`, `intent_created_at`, `maximum_steps`, `plan_expiry_at`, audit
`created_at` (excluded from identity). The expiry boundary is derived once:

```text
plan_expiry_at = intent_created_at
               + step_window_duration * maximum_steps
               + step_gap * (maximum_steps - 1)
```

`FillEvaluationStep`: `fill_evaluation_step_id`, `step_contract_version`,
`fill_evaluation_plan_id`, `ordinal: int >= 0`, `evaluation_window_start_at`,
`evaluation_due_at`, `remaining_quantity_before: Decimal` (positive finite),
`fill_policy_id`, audit `created_at` (excluded from identity). Windows:

```text
Step 0: start = intent_created_at
Step n: start = due(n-1) + step_gap
Every Step: due = start + step_window_duration
```

Both bounds are inclusive in the eligibility predicate. Because `step_gap > 0`, no
two Steps of one plan overlap.

```text
remaining_quantity_before(n) = original_quantity
                             - sum(fill_quantity of persisted Fills of Steps 0..n-1)
```

`FillEvaluationAttempt`: `fill_evaluation_attempt_id`, `fill_evaluation_step_id`,
`evaluated_at`, `disposition` (only `PENDING_NO_ELIGIBLE_MARKET` exists in v1),
`diagnostic_code: PaperAttemptDiagnosticCode`, `worker_identity: str`, audit
`created_at`. The identity payload includes `evaluated_at` and `worker_identity` so
one Step may hold many distinct immutable attempts.

`worker_identity` is a non-blank exact `str` supplied once to the application-service
constructor, beside the `Clock`, and is constant for that service instance. It is
never an entry-point argument, never read from the environment, the process ID, the
host name, or any other ambient source, and never a hardcoded module constant. Two
attempts therefore differ by `evaluated_at` within one worker and by
`worker_identity` between workers.

`PaperAttemptDiagnosticCode` is a `StrEnum` with exactly these two values, derived by
B2 from the candidate set defined in "Deterministic market selection" and never
supplied by a caller, in this frozen precedence:

```text
NO_OBSERVATION_FOR_PAIR      no persisted observation has the plan's Pair
ALL_OBSERVATIONS_INELIGIBLE  at least one persisted observation has the plan's Pair
                             and every one of them failed at least one eligibility
                             clause
```

There is no "nothing was supplied" code, because the candidate set is the persisted
set and never the call's argument tuple.

`PaperMarketObservationSelection`: `market_observation_selection_id`,
`fill_evaluation_step_id`, `fill_evaluation_plan_id`, `market_observation_id`,
`market_selection_policy_version`, `evaluation_window_start_at`,
`evaluation_due_at`, `intent_created_at`, audit `selected_at` (excluded).

`PaperNoMarketOutcome`: `no_market_outcome_id`, `fill_evaluation_step_id`,
`terminal_reason_code` = `"REJECTED_NO_MARKET_EVIDENCE"`, `evaluation_due_at`, audit
`resolved_at` (excluded).

`PaperFill`: `paper_fill_id`, `fill_contract_version`, `fill_evaluation_step_id`,
`market_observation_selection_id`, `market_observation_id`, `pair`, `side`,
`fill_quantity`, `fill_price`, `reference_price`, `slippage_basis_points`,
`fill_model_version`, `remaining_quantity_before`, `remaining_quantity_after`, audit
`created_at` (excluded).

### Deterministic market selection

**Frozen candidate set.** The candidate observations for a Step are exactly the rows
of `live_paper_market_observations`. They are never the tuple supplied to the current
call, and never a union of the two. Observations a caller supplies reach selection only
by first being append-or-compare persisted in T0, which the application service runs
before T3 in the same call; an observation that T0 did not persist is invisible to
selection. This makes one call's candidate set independent of which observations that
particular call happened to carry, so two callers replaying the same intent against the
same database always select the same observation.

B4 owns the single hydration query and is the only place a candidate set is produced:

```sql
SELECT * FROM live_paper_market_observations
WHERE pair = :plan_pair
  AND received_at >= :intent_created_at
  AND received_at >= :step_window_start_at
  AND received_at <= :step_evaluation_due_at
  AND received_at <= :evaluated_at
  AND provider_observed_at <= received_at
  AND provider_observed_at <= :step_evaluation_due_at
  AND provider_observed_at >= :step_evaluation_due_at_minus_maximum_market_age
  AND market_observation_id NOT IN (
        SELECT market_observation_id FROM live_paper_market_observation_selections
        WHERE fill_evaluation_plan_id = :plan_id)
ORDER BY received_at ASC, provider_observed_at ASC, market_observation_id ASC
LIMIT 1
```

The hydrated row is then rebuilt as an exact `PaperMarketObservation` and revalidated
by B2 against the complete predicate below; a row that the SQL returned but the
predicate rejects is persisted corruption and fails closed. B2's pure predicate and
ordering operate on a supplied tuple so they remain unit-testable, but in the composed
path that tuple is only ever the hydrated candidate set.

An observation `o` is eligible for Step `s` of plan `p` at `evaluated_at` if and only
if all of the following hold. Every clause is a separate fail-closed check.

1. `type(o) is PaperMarketObservation` and its base validator passes.
2. `o.pair == p.pair`.
3. `p.intent_created_at <= o.received_at`.
4. `s.evaluation_window_start_at <= o.received_at`.
5. `o.received_at <= s.evaluation_due_at`.
6. `o.received_at <= evaluated_at` (locally available at the resolution attempt).
7. `o.provider_observed_at <= o.received_at` and
   `o.provider_observed_at <= s.evaluation_due_at`.
8. `s.evaluation_due_at - o.provider_observed_at <= policy.maximum_market_age`.
   Equality is eligible; strictly greater is stale. Freshness deliberately does not
   read `evaluated_at`, so eligibility is replay-stable.
9. `o.market_observation_id` is not present in any persisted
   `PaperMarketObservationSelection` of the same plan.

Selection order over the eligible set:

```sql
ORDER BY received_at ASC, provider_observed_at ASC, market_observation_id ASC
LIMIT 1
```

The in-memory ordering uses the same tuple
`(received_at, provider_observed_at, market_observation_id)` on hydrated exact
`datetime` and `str` values and must agree with the SQL ordering. Clause 9 is
additionally enforced by a database `UNIQUE(fill_evaluation_plan_id,
market_observation_id)`; because selections are unique per plan, "not selected by an
earlier Step" and "not selected by any other Step of this plan" are equivalent.

Once a Step's terminal selection is first-written, retry hydrates it and never
searches again, even if a newer or better observation is now eligible.

### Deterministic fill computation

Execution reference price: BUY uses `observation.ask`, SELL uses `observation.bid`.

Slippage is adverse and explicit, computed under `paper-exact-arithmetic-v1` with no
division:

```text
BUY : fill_price = ask + ask * slippage_basis_points * Decimal("0.0001")
SELL: fill_price = bid - bid * slippage_basis_points * Decimal("0.0001")
```

`fill_price` must be a positive finite `Decimal`; a non-positive or non-finite result
fails closed. `reference_price`, `slippage_basis_points`, and `fill_model_version`
are retained in the Fill so the price is reproducible from evidence alone.

Fill quantity is decided only by the versioned policy:

```text
FULL_REMAINING        : fill_quantity = remaining_quantity_before
FRACTION_OF_REMAINING : fill_quantity = remaining_quantity_before * partial_fill_fraction
```

Invariants, all checked before the Fill is constructed:

```text
0 < fill_quantity <= remaining_quantity_before
remaining_quantity_after = remaining_quantity_before - fill_quantity
sum(ordered Fills of the plan) <= original_quantity
```

A zero or negative computed quantity creates no `PaperFill` and fails closed. There
is no implicit liquidity model and no default.

The next Step is created only when all of these hold: the directly preceding Step
resolved `MARKET_SELECTED` with a positive Fill, `remaining_quantity_after > 0`, and
`ordinal + 1 < maximum_steps`. Ordinal skip, duplicate ordinal, and speculative
future Steps are forbidden. A terminal order state forbids any new Step.

### No-market behaviour

With no eligible observation for Step `s`:

- `evaluated_at < s.evaluation_due_at` -> append one immutable
  `FillEvaluationAttempt` with `PENDING_NO_ELIGIBLE_MARKET`. The Step stays
  unresolved and a later pre-due evaluation may still select an eligible quote for
  the same Step.
- `evaluated_at >= s.evaluation_due_at` -> first-write the terminal claim with
  variant `NO_MARKET` and one `PaperNoMarketOutcome`.

After the terminal first-write, a late or late-processed quote never rewrites
history: it can neither create a selection for that Step nor a second resolution.

## Frozen accounting model

### Settlement scope

The configured Strategy Pairs `USD_JPY` and `MXN_JPY` are both JPY-quoted, so the M3
v1 accounting settlement currency is exactly `Currency("JPY")` and every quote-side
amount is already a settlement-currency amount. M3 implements no FX conversion and
contains no conversion function. Any Pair whose quote currency is not JPY fails
closed as unsupported at the order entry point. Expanding the Pair set requires a new
accounting contract version and a new design.

Quantity unit is exact base-currency units (`BASE_UNITS`), matching M2-D capacity.
Price is settlement currency per one base unit, so `price * quantity` is a
settlement-currency amount.

### `PaperAccountBootstrap`

Immutable, content-addressed
(`paper_account_id = "paper-account-" + digest(...)`), no production default:

- `bootstrap_contract_version` = `"paper-account-bootstrap-v1"`;
- `initial_cash: Decimal`, finite, `> 0`;
- `settlement_currency: Currency`, must be `JPY`;
- `margin_policy_version: str`;
- `leverage: Decimal`, finite, `> 0`;
- `unrealized_mark_policy_version: str`.

### Position projection

`PaperPositionFillApplication` records, ordered by a monotonic `application_seq`, are
the position ledger. `PaperPositionApplicationKind` is a `StrEnum` with exactly
`ENTRY` and `REDUCE_ONLY`. The record's exact fields are:

- `paper_position_fill_application_id` =
  `"paper-position-application-" + digest(identity payload)`;
- `application_contract_version` = `"paper-position-fill-application-v1"`;
- `paper_position_id: str`;
- `paper_order_id: str` — the owning order, so `application_kind` and
  `paper_position_id` are both regenerable from persisted lineage;
- `paper_fill_id: str`, bound exactly once across all applications;
- `application_kind: PaperPositionApplicationKind`, which is `ENTRY` when the owning
  order's `intent_lineage.intent_kind` is `ENTRY` and `REDUCE_ONLY` for the other two
  kinds — it is derived from that lineage and never independently supplied;
- `quantity: Decimal` and `price: Decimal`, both positive finite, exactly the bound
  Fill's `fill_quantity` and `fill_price`;
- `open_quantity_after: Decimal`, finite and never negative;
- `realized_pnl_amount: Decimal | None`, exactly `None` for `ENTRY` and a finite
  `Decimal` for `REDUCE_ONLY`;
- audit `created_at`, excluded from identity.

The identity payload is every field above except the ID and `created_at`, in that
order. `application_seq` is assigned by the database at insert and is therefore never
part of the identity.

- A Paper position is created by the first `ENTRY` application; its side is `LONG`
  when the entry order side is `BUY` and `SHORT` when it is `SELL`. The
  `live_paper_positions` row is written inside the T3a or T3b transaction that writes
  that first `ENTRY` application, immediately before the application row, by
  append-or-compare. Its exact columns are `paper_position_id`,
  `position_contract_version`, `paper_account_id`, `entry_paper_order_id`, `pair`,
  `position_side`, and audit `created_at`; every one of them is copied from the entry
  order and none is derived later. A second entry Fill for the same position re-reads
  and compares that row instead of inserting. No other transaction ever creates,
  updates, or deletes a position row, and a `REDUCE_ONLY` application whose position
  row is absent is a missing-parent integrity failure rather than a row-creating path.
- Partial entry Fills of the same entry intent accumulate into the same position
  because they share one `paper_position_id`.
- `open_quantity = sum(ENTRY quantities) - sum(REDUCE_ONLY quantities)` over the
  ordered applications, and must never be negative.
- A reduce-only application with `quantity > open_quantity` before it fails closed as
  a ledger integrity error; the whole transaction rolls back and no position flips.
- **An `ENTRY` application on a position that already holds at least one
  `REDUCE_ONLY` application fails closed as a ledger integrity error.** One position's
  applications are therefore always a contiguous run of `ENTRY` applications followed
  by a contiguous run of `REDUCE_ONLY` applications. This is what makes
  `paper-weighted-average-entry-price-v1` equivalent to weighted-average-cost
  accounting: no later entry can retroactively move a basis that an earlier close has
  already realized against. Every close of one position therefore prices against one
  fixed basis, and when that basis is exactly representable under
  `paper-quotient-arithmetic-v1` realized PnL over a fully closed position equals the
  signed cash-flow result
  `side_sign * (sum(close price * qty) - sum(entry price * qty))` exactly. When the
  basis is not exactly representable — for example entry fills of 100 at 100 and 200
  at 101, whose average does not terminate — the total differs from that identity only
  by the 34-significant-digit half-even rounding of the basis multiplied by the total
  closed quantity. No further error is introduced, and the residue is a property of
  the frozen quotient context rather than of the ordering rule. A multi-Step entry
  order whose later Step would fill
  after a close or liquidation already reduced the position is rejected at the ledger
  boundary; the whole transaction rolls back, no Fill, application, ledger entry,
  snapshot, or reservation row is written, and the Step's terminal claim is not taken.
- Position state is `OPEN` while `open_quantity > 0` and `CLOSED` at exactly zero.

### Formulas

`paper-weighted-average-entry-price-v1` (`paper-quotient-arithmetic-v1`):

```text
average_entry_price(at seq S)
  = sum(price * quantity for ENTRY applications with application_seq < S)
  / sum(quantity      for ENTRY applications with application_seq < S)
```

Reduce-only applications never change the average entry price. The value is always
recomputed from the complete ordered ENTRY application set, never carried in a
mutable field. Because no `ENTRY` application may follow a `REDUCE_ONLY` application
on one position, "the ENTRY applications with `application_seq < S`" and "all ENTRY
applications of the position" are the same set at every reduce-only sequence `S`, so
this rule cannot retroactively revalue an already-realized close.

`paper-realized-pnl-v1` (`paper-exact-arithmetic-v1`), for each `REDUCE_ONLY`
application at sequence `S` with close price `P` and quantity `Q`:

```text
LONG  position: realized_pnl = (P - average_entry_price(at S)) * Q
SHORT position: realized_pnl = (average_entry_price(at S) - P) * Q
```

`paper-account-mark-set-v1` fixes where marks come from, because latest-row or
implicit-current lookup is a frozen non-goal. Every account-level aggregate is
computed against one exact `PaperAccountMarkSet`:

- it is an ordered tuple of exact `PaperMarketObservation` values, sorted by
  `pair.symbol`;
- it contains exactly one observation per Pair in the **required coverage set**
  defined below — no missing Pair, no duplicate Pair, and no extra Pair;
- every mark satisfies `received_at <= bounding_instant`, so no future evidence can
  value the account. The bounding instant is the Clock-sourced `evaluated_at` in every
  transaction driven by an application-service entry point (T1 through T4), and is the
  transaction's own audit instant in the swap-rollover (T5) and swap-correction (T6)
  transactions, which have no entry point. The rule is identical in all of them: a
  mark received after the bounding instant fails closed. T5 and T6 constrain that
  audit instant exactly as B4 constrains any supplied instant — exact `datetime`,
  UTC-aware, and no earlier than the greatest instant in the frozen T5/T6/T7
  non-regression scan set — and M4 must source it from the same `Clock`, so a
  substituted time source remains the single controlled boundary defined in "Frozen
  time source" rather than a second unbounded input;
- every mark already exists as a persisted `live_paper_market_observations` row. A
  Fill-applying transaction relies on the T0 its own call ran; T5 and T6 write no
  market observation and require an earlier committed T0. A mark with no persisted row
  is a missing-parent integrity failure. The account snapshot identity records the
  ordered tuple of their `market_observation_id` values.

**Required coverage set.** The set is fixed by state that exists before the writing
transaction applies anything, so a caller can always satisfy it without predicting
the outcome of the call:

```text
coverage_set = { pair of every position of this account whose open quantity is
                 strictly positive immediately BEFORE this transaction's
                 position fill applications }
             union
               { the order's Pair }        (only for a transaction that applies a Fill)
```

For a transaction that applies no Fill — the swap-rollover transaction and the
swap-correction transaction — the coverage set is exactly the first term, evaluated at
the start of that transaction, and there is no order Pair.

Because one transaction applies at most one Fill, and that Fill can only open or
reduce the order's own Pair, every Pair holding an open position **after** the
applications is always inside the coverage set. Consequently:

- A reduce-only Fill that closes a position to exactly zero still requires a mark for
  that Pair, because the Pair was open before the applications. That mark contributes
  nothing to any aggregate and the Pair is not counted in `open_position_count`, but
  its absence is a missing-Pair integrity failure.
- An entry Fill that opens a new position requires a mark for the order's Pair even
  though no position existed before.
- A Pair that is neither open before nor the order's Pair is an extra Pair and fails
  closed.

Every aggregate is computed only over positions whose open quantity is strictly
positive **after** this transaction's applications; a covered Pair with zero open
quantity after contributes exactly zero and is excluded from `open_position_count`.

A mark set that omits a coverage-set Pair, repeats a Pair, includes a Pair outside the
coverage set, or contains an observation received after the bounding instant is an
integrity failure that rolls the whole transaction back. An aggregate is never silently computed
over a subset of open positions. The observation selected for the Step's own execution
is not implicitly reused as a mark; the mark for that Pair is supplied like any other
and may differ.

The caller supplies the mark set to the B5 entry point; B4 validates coverage inside
the transaction; reconciliation rebuilds using exactly the `market_observation_id`
tuple recorded in the persisted snapshot, never a freshly chosen mark set.

`paper-unrealized-pnl-v1` (`paper-exact-arithmetic-v1`), per open position, using the
mark-set observation for its Pair:

```text
LONG : unrealized_pnl = (observation.bid - average_entry_price) * open_quantity
SHORT: unrealized_pnl = (average_entry_price - observation.ask) * open_quantity
```

`paper-gross-exposure-v1` (`paper-exact-arithmetic-v1`), using the same side-specific
mark price (`bid` for LONG, `ask` for SHORT):

```text
gross_exposure = sum(abs(mark_price * open_quantity)) over open positions
```

`paper-account-equity-v1` (`paper-exact-arithmetic-v1`). Cash, realized PnL, accrued
swap, and unrealized PnL are four independent aggregates and are never conflated:

```text
cash              = bootstrap.initial_cash          (M3 never mutates cash)
realized_pnl_total = sum(ledger amounts of kind REALIZED_PNL)
accrued_swap_total = sum(ledger amounts of kinds SWAP_ACCRUAL
                                              and SWAP_ACCRUAL_CORRECTION)
unrealized_pnl_total = sum(paper-unrealized-pnl-v1 over open positions)
equity = cash + realized_pnl_total + accrued_swap_total + unrealized_pnl_total
```

`paper-used-margin-v1` (`paper-quotient-arithmetic-v1`) and
`paper-available-margin-v1` (`paper-exact-arithmetic-v1`):

```text
used_margin      = gross_exposure / bootstrap.leverage
available_margin = equity - used_margin
```

`available_margin` may be negative; M3 reports it and adds no margin call.

`paper-open-position-count-v1` and `paper-open-order-count-v1` are the two cardinality
aggregates, both defined at explicit boundaries:

```text
open_position_count = count of positions of this paper_account_id whose open quantity
                      projected over applications with application_seq <= boundary
                      is strictly positive

open_order_count    = count of orders of this paper_account_id that have at least one
                      order event with order_event_seq <= boundary AND whose state,
                      projected from exactly those events, is one of ACCEPTED, OPEN,
                      PARTIALLY_FILLED (that is, not one of the terminal states
                      FILLED, CANCELLED, EXPIRED, REJECTED)
```

An order with no order event at or below the boundary did not yet exist at that
boundary. It is excluded from `open_order_count` before any projection is attempted;
`project_paper_order_state` is never called on the empty truncated event set, so
reconciling an older snapshot after a newer order was created returns a typed
`MATCHED`/`MISMATCHED` result and never a projection integrity error.

`PaperAccountSnapshot` retains exactly `paper_account_snapshot_id`,
`snapshot_contract_version`, `paper_account_id`, `cash`, `realized_pnl_total`,
`unrealized_pnl_total`, `accrued_swap_total`, `equity`, `used_margin`,
`available_margin`, `gross_exposure`, `open_position_count`, `open_order_count`, the
ordered mark `market_observation_id` tuple, the three boundaries
`highest_application_seq`, `highest_ledger_entry_seq`, and `highest_order_event_seq`,
every formula/policy version named above, and audit `created_at` (excluded from
identity). Its ID commits to every other field. The three boundaries are the exact
values in effect at the end of the writing transaction, so each retained aggregate has
a boundary that makes it reconstructible.

`PaperPositionSnapshot` retains exactly `paper_position_snapshot_id`,
`snapshot_contract_version`, `paper_account_id`, `paper_position_id`, `pair`, position
side, `open_quantity`, `average_entry_price`, `realized_pnl_total`,
`accrued_swap_total`, the two boundaries `highest_application_seq` and
`highest_ledger_entry_seq`, its formula versions, and audit `created_at` (excluded
from identity). `realized_pnl_total` is the sum over that position's applications up
to `highest_application_seq`; `accrued_swap_total` is the sum of that position's
`SWAP_ACCRUAL` and `SWAP_ACCRUAL_CORRECTION` ledger entries up to
`highest_ledger_entry_seq`. Two snapshots of one position at identical boundaries and
values have identical content and therefore one identity, so append-or-compare handles
a repeated write.

**Account linkage.** `paper_account_id` is carried on `PaperOrder`, on the Paper
position (inherited from the entry order that created it), on every
`PaperLedgerEntry`, and on both snapshot kinds. The account-to-position and
account-to-order relations are those explicit columns; no relation is inferred, and
`reconciled_position_ids` is exactly the set of positions carrying that
`paper_account_id`.

### Ledger entries

ExecPlan 0006 describes Paper ledger entries as append-only and balanced. M3 v1
deliberately narrows that to a single-sided append-only ledger because cash is held
constant at the bootstrap value and nothing is settled, so every counter-entry would
be a constant zero-information row. The deviation, its rationale, and the condition
that reopens the balanced-entry requirement are recorded in the ExecPlan 0006
Decision Log dated 2026-08-07.

`PaperLedgerEntry` is append-only, single-sided, and denominated in JPY:
`ledger_entry_id`, `entry_contract_version`, `paper_account_id`,
`paper_position_id`, `entry_kind: PaperLedgerEntryKind` in
`{REALIZED_PNL, SWAP_ACCRUAL, SWAP_ACCRUAL_CORRECTION}`, `settlement_currency`
(JPY), `amount: Decimal` (finite, signed, may be zero or negative),
`source_evidence_kind`, `source_evidence_id`, `formula_version`, audit `created_at`.
One `(entry_kind, source_evidence_id)` pair may appear at most once, so a fill's
realized PnL and a rollover's accrual can never be double-posted. Entries carry a
monotonic `ledger_entry_seq`; account aggregates are exactly the sums above.

### Swap accrual

`PaperSwapAccrualPolicy` is immutable and versioned:

- `policy_contract_version` = `"paper-swap-accrual-policy-v1"`;
- `policy_version: str`;
- `formula_version: str` = `"paper-swap-accrual-v1"`;
- `unit_basis_base_units: tuple[tuple[str, Decimal], ...]` — a non-empty ordered
  mapping from a supported exact `str` `unit_basis` value to the positive finite
  `Decimal` number of base units that one quoted swap amount applies to. Keys are
  unique exact `str`; no default entry exists;
- `maximum_swap_age: timedelta`, strictly positive;
- `settlement_currency: Currency`, must be `JPY`.

`rollover_date` is an exact `datetime.date`, never a `datetime` and never a string.
Under `paper-swap-rollover-instant-v1` its instant is derived exactly once:

```text
rollover_at = datetime(rollover_date.year, rollover_date.month, rollover_date.day,
                       0, 0, 0, 0, tzinfo=UTC)
```

M3 v1 deliberately does not model a broker's local rollover hour, a value date
convention, or daylight-saving transitions; the rollover instant is the UTC midnight
that begins that date. Any other convention requires a new rollover-instant version.

`paper-swap-accrual-v1`, for one open position at one rollover date, where
`received_amount` is `evidence.long_received_amount` for a LONG position and
`evidence.short_received_amount` for a SHORT position:

```text
quantity_ratio = open_quantity / base_units_for(evidence.unit_basis)   (quotient)
accrual_amount = received_amount * quantity_ratio                     (exact)
```

`PaperSwapAccrual` does not change `OperationalSwapEvidence`. It commits to
`paper_position_id`, the exact `paper_position_snapshot_id` (the exact position
state), `swap_evidence_id`, `rollover_date`, `open_quantity`, `unit_basis`,
`base_units_per_unit`, `settlement_currency`, `policy_version`, `formula_version`,
and `amount`.

**Where the bound position snapshot comes from.** `paper_position_snapshot_id` is an
exact caller-supplied identifier passed to the accrual entry point; it is never
selected by the store, and no `ORDER BY ... DESC LIMIT 1`, "most recent", "current",
or other latest-row lookup may resolve it. Inside the transaction B4 authenticates it:

- the snapshot row exists and was committed by an earlier transaction;
- its `paper_position_id` equals the accrual's position;
- its `open_quantity` equals the accrual's `open_quantity`, and that value — not a
  freshly projected one — is what the formula consumes;
- no `PaperPositionFillApplication` exists for that position with
  `application_seq > snapshot.highest_application_seq`, and no `SWAP_ACCRUAL` or
  `SWAP_ACCRUAL_CORRECTION` ledger entry exists for that position with
  `ledger_entry_seq > snapshot.highest_ledger_entry_seq`. A caller naming a superseded
  snapshot therefore fails closed instead of accruing against stale state.

An accrual never binds a snapshot produced by its own transaction. The snapshots the
accrual transaction writes are its outputs and reflect the accrual; the snapshot the
accrual commits to is the pre-existing state the accrual was computed from. This also
removes the circularity created by `PaperPositionSnapshot` retaining
`accrued_swap_total`.

`PaperSwapAccrualOutcome` is a `StrEnum` with exactly:

```text
ACCRUED
NOT_ACCRUED_SWAP_MISSING
NOT_ACCRUED_SWAP_UNAVAILABLE
NOT_ACCRUED_SWAP_STALE
NOT_ACCRUED_UNSUPPORTED_UNIT_BASIS
NOT_ACCRUED_UNSUPPORTED_SETTLEMENT_CURRENCY
NOT_ACCRUED_PAIR_MISMATCH
NOT_ACCRUED_POSITION_NOT_OPEN
```

Frozen precedence, evaluated in this order, first match wins:

1. no evidence supplied -> `NOT_ACCRUED_SWAP_MISSING`;
2. `evidence.pair != position.pair` -> `NOT_ACCRUED_PAIR_MISMATCH`;
3. `evidence.availability is not SwapAvailability.AVAILABLE` ->
   `NOT_ACCRUED_SWAP_UNAVAILABLE`;
4. `evidence.settlement_currency != JPY` ->
   `NOT_ACCRUED_UNSUPPORTED_SETTLEMENT_CURRENCY`;
5. `evidence.unit_basis` absent from the policy mapping ->
   `NOT_ACCRUED_UNSUPPORTED_UNIT_BASIS`;
6. any of the following -> `NOT_ACCRUED_SWAP_STALE`:
   - `rollover_at < evidence.effective_from`;
   - `evidence.effective_until is not None` and
     `rollover_at > evidence.effective_until`. When `effective_until is None` the
     evidence is open-ended and no upper window bound is tested;
   - `rollover_at < evidence.received_at`, so evidence that did not yet exist at the
     rollover instant can never accrue (no lookahead);
   - `rollover_at - evidence.received_at > policy.maximum_swap_age`.

   Both window endpoints and the age equality are inclusive and remain eligible;
7. `open_quantity <= 0` -> `NOT_ACCRUED_POSITION_NOT_OPEN`;
8. otherwise `ACCRUED`.

Every non-accrual persists one typed `PaperSwapNonAccrual` record with its outcome
and reason, creates no `PaperSwapAccrual`, and creates no ledger entry. A zero
`PaperSwapAccrual` is never written to represent a non-accrual.

A historical correction is `PaperSwapAccrualCorrection`: `correction_id`,
`correction_contract_version`, `corrected_accrual_id` (an existing exact accrual),
`chain_ordinal: int`, `predecessor_correction_id: str | None`,
`effective_amount_before`, `replacement_amount`, `delta_amount`, `correction_reason`,
`swap_evidence_id`, and audit `created_at` (excluded from identity).

Corrections of one accrual form one contiguous chain. `chain_ordinal` starts at `1`
for the first correction and increases by exactly one; `predecessor_correction_id` is
`None` at ordinal `1` and otherwise the `correction_id` at `chain_ordinal - 1`. The
deterministic chain order is `chain_ordinal`, which is content-bearing; the table's
`correction_seq` is an insert-order audit column only and never orders the chain and
never enters an identity.

```text
correction_id = "paper-swap-correction-" + digest({
    correction_contract_version, corrected_accrual_id, chain_ordinal,
    predecessor_correction_id, effective_amount_before, replacement_amount,
    delta_amount, correction_reason, swap_evidence_id })
```

Because `chain_ordinal` and `predecessor_correction_id` are inside the identity, a
chain that returns to a previously held effective amount produces a distinct
`correction_id` and a distinct ledger entry; a later correction can never collide with
an earlier one, be silently reused by append-or-compare, or be dropped.

The delta is computed against the current effective amount, never against the original
amount:

```text
effective_amount_before = accrual.amount
                        + sum(delta_amount of corrections of that accrual with a
                              strictly smaller chain_ordinal)
delta_amount            = replacement_amount - effective_amount_before
```

Each correction appends exactly one `SWAP_ACCRUAL_CORRECTION` ledger entry whose
amount is `delta_amount` and whose `source_evidence_id` is that `correction_id`, so
`UNIQUE(entry_kind, source_evidence_id)` still forbids double-posting one correction.
The cumulative effect of an accrual plus its ordered corrections is therefore exactly
the last `replacement_amount`; a chain can never double-count. The original accrual
row, every earlier correction row, every earlier ledger entry, and the rollover claim
are never updated or deleted.

### Reconciliation

`PaperReconciledRecordKind` is a `StrEnum` with exactly `POSITION_FILL_APPLICATION`,
`LEDGER_ENTRY`, `POSITION_SNAPSHOT`, and `ACCOUNT_SNAPSHOT`.
`PaperReconciliationOutcome` is a `StrEnum` with exactly `MATCHED` and `MISMATCHED`.

`PaperReconciliationResult` exact fields:

- `reconciliation_result_id` = `"paper-reconciliation-" + digest(identity payload)`;
- `result_contract_version` = `"paper-reconciliation-result-v1"`;
- `paper_account_id: str`;
- `outcome: PaperReconciliationOutcome`;
- `reconciled_position_ids: tuple[str, ...]`, ascending, exactly the positions whose
  `paper_account_id` column equals this account;
- `highest_application_seq: int`, `highest_ledger_entry_seq: int`, and
  `highest_order_event_seq: int`, the compared boundaries;
- `mismatched_record_kinds: tuple[PaperReconciledRecordKind, ...]`, ascending by
  value, without duplicates;
- `mismatched_record_ids: tuple[str, ...]`, ascending;
- audit `created_at` (excluded from identity).

`outcome is MATCHED` if and only if both mismatch tuples are empty.

Frozen comparison scope. For one `paper_account_id`, reconciliation rebuilds and
compares all four record kinds; omitting any kind is not a valid implementation:

Each rebuild names its complete input set. A rebuild that regenerates only the subset
of fields some smaller input set happens to support is not a valid implementation;
every retained field of every compared record is regenerated and compared.

1. `POSITION_FILL_APPLICATION`. Inputs: that position's applications ordered by
   `application_seq`; the `PaperFill` each one binds; and the owning `PaperOrder`
   named by each application's `paper_order_id`, together with its
   `intent_lineage`. Regenerate every retained field: `paper_position_id` must equal
   the owning order's `intent_lineage.paper_position_id`; `application_kind` must
   equal `ENTRY` when that lineage's `intent_kind` is `ENTRY` and `REDUCE_ONLY`
   otherwise; `quantity` and `price` must equal the bound Fill's `fill_quantity` and
   `fill_price`; `open_quantity_after` and, for `REDUCE_ONLY`,
   `realized_pnl_amount` are recomputed with
   `paper-weighted-average-entry-price-v1` and `paper-realized-pnl-v1` over the
   strictly earlier applications; and the application ID is recomputed from the
   regenerated content. Reconstruction also reasserts the ordering rule: an `ENTRY`
   application appearing after any `REDUCE_ONLY` application of that position is a
   mismatch. A close Fill persisted as an `ENTRY` application therefore cannot
   reconcile, because its kind disagrees with its owning order's lineage.
2. `LEDGER_ENTRY`. Inputs: each entry's exact source evidence — the position fill
   application of the `PaperFill` for `REALIZED_PNL`, the `PaperSwapAccrual` for
   `SWAP_ACCRUAL`, and the `PaperSwapAccrualCorrection` for
   `SWAP_ACCRUAL_CORRECTION`. Recompute the amount and compare; require the entry's
   `paper_position_id` to equal its source evidence's position; and require the
   entry's `paper_account_id` to equal that position's `paper_account_id` read from
   `live_paper_positions`, never merely the account the writing order claimed. An
   entry posted to any other account is a mismatch.
3. `POSITION_SNAPSHOT`. Inputs: that position's row (account, Pair, side), its
   applications with `application_seq <= snapshot.highest_application_seq`, and its
   `SWAP_ACCRUAL` plus `SWAP_ACCRUAL_CORRECTION` ledger entries with
   `ledger_entry_seq <= snapshot.highest_ledger_entry_seq`. Rebuild `open_quantity`,
   `average_entry_price`, `realized_pnl_total`, and `accrued_swap_total`, recompute
   the snapshot ID, and compare every retained field.
4. `ACCOUNT_SNAPSHOT`. Inputs: the `PaperAccountBootstrap` (cash, leverage,
   versions); every position of that account and its applications with
   `application_seq <= snapshot.highest_application_seq`; every ledger entry of that
   account with `ledger_entry_seq <= snapshot.highest_ledger_entry_seq`; the
   observations hydrated by the snapshot's recorded mark-observation tuple; and every
   order event of that account's orders with
   `order_event_seq <= snapshot.highest_order_event_seq`. Rebuild `cash`,
   `realized_pnl_total`, `accrued_swap_total`, `unrealized_pnl_total`,
   `gross_exposure`, `equity`, `used_margin`, `available_margin`,
   `open_position_count`, and `open_order_count`, recompute the snapshot ID, and
   compare every retained field. Every one of those ten aggregates is individually
   regenerated and individually compared.

`MISMATCHED` is a typed result, never a repair, never an UPDATE, and never a
reclassification of a business outcome. The reconciliation transaction commits
exactly one result row in both outcomes.

B3 owns the `PaperReconciliationResult`, `PaperReconciliationOutcome`, and
`PaperReconciledRecordKind` contracts and the pure rebuild-and-compare functions. B4
owns hydrating the persisted records, invoking those functions, and persisting the
result.

## Frozen reservation settlement model

Only typed M3 evidence may reduce an M2-D ordinary-close reservation. The join key to
M2-D is the exact `ApprovedCloseIntent.idempotency_key`, which is precisely the
`intent_id` value that M2-D's `OrdinaryCloseReservationSnapshot` entries carry.

Before any ordinary-close Paper order, plan, consumption, or release is written, B4
hydrates the exact persisted M2-D row from `live_ordinary_close_approved_intents` for
that `idempotency_key` and requires its full content — Candidate, Portfolio, Risk,
capacity, Position, Pair, Side, quantity, authority, and creation time — to equal the
supplied `ApprovedCloseIntent`. A supplied intent with no persisted M2-D row, or with
different persisted content, is an integrity failure. The conservation equation uses
the persisted quantity, never the supplied object's quantity. M3 writes to no M2-D
table.

`ReservationConsumptionEvidence`: `consumption_id`, `contract_version`,
`close_intent_idempotency_key`, `paper_order_id`, `paper_fill_id`,
`consumed_quantity` (exactly the Fill quantity), audit `created_at`.

`ReservationReleaseEvidence`: `release_id`, `contract_version`,
`close_intent_idempotency_key`, `paper_order_id`, `terminal_order_state` in
`{CANCELLED, EXPIRED, REJECTED}`, `released_quantity`, audit `created_at`.

Conservation, per `ApprovedCloseIntent`:

```text
consumed_total = sum(ReservationConsumptionEvidence.consumed_quantity)
released_total = sum(ReservationReleaseEvidence.released_quantity)   (0 or 1 record)
outstanding    = intent.quantity - consumed_total - released_total

consumed_total + outstanding + released_total == intent.quantity
consumed_total <= intent.quantity
outstanding >= 0
```

`consumed_total` is always evaluated inside the writing transaction and includes
every consumption row that transaction has already written, not only rows committed
by earlier transactions. When one transaction writes both a consumption and a
release, the consumption is written first and `released_quantity` is computed from
the updated `consumed_total`. Reading `consumed_total` from committed rows only is
prohibited, because it double-counts the same quantity as consumed and released.

Frozen authorisation rules:

- A `PaperFill` on an ordinary-close order appends exactly one consumption of exactly
  the filled quantity, in the same transaction as the Fill and its ledger
  application. A partial Fill consumes only the filled quantity and never releases
  the remainder.
- A release is authorised only by a terminal order event of `CANCELLED`, `EXPIRED`,
  or `REJECTED` and is appended in the same transaction as that event, with
  `released_quantity = intent.quantity - consumed_total` which must be strictly
  positive.
- Terminal `FILLED` authorises no release. A PENDING attempt, a process crash, a
  retry, a new M2-D capacity observation, a later reservation snapshot, and the
  passage of time authorise no release.
- Entry and emergency-liquidation orders create no consumption or release evidence at
  all; M2-D reservations exist only for ordinary close.

Fail-closed cases: a second consumption for one Fill, a second release for one order,
any consumption after a release exists for that order, a release whose quantity is
not exactly the remainder, and any consumption that would make `consumed_total`
exceed `intent.quantity`.

## Frozen persistence model

Live migration `0006_paper_execution_ledger.sql` is the next available additive
number after `0005`. Existing migrations `0001` through `0005` are not edited.

Tables (all prefixed `live_paper_`), each with UPDATE and DELETE rejection triggers:

```text
market_observations            PK market_observation_id
fill_policies                  PK paper_fill_policy_id
account_bootstraps             PK paper_account_id
orders                         PK paper_order_id;
                               paper_account_id REFERENCES account_bootstraps;
                               UNIQUE(intent_kind, source_intent_id);
                               UNIQUE(intent_kind, source_intent_idempotency_key)
order_events                   order_event_seq INTEGER PRIMARY KEY AUTOINCREMENT;
                               UNIQUE(paper_order_id, event_ordinal);
                               UNIQUE(paper_order_event_id)
fill_evaluation_plans          PK fill_evaluation_plan_id; UNIQUE(paper_order_id)
fill_evaluation_steps          PK fill_evaluation_step_id;
                               UNIQUE(fill_evaluation_plan_id, ordinal)
fill_evaluation_attempts       PK fill_evaluation_attempt_id (many per Step)
step_terminal_claims           fill_evaluation_step_id TEXT PRIMARY KEY
                                 REFERENCES fill_evaluation_steps;
                               variant TEXT NOT NULL
                                 CHECK IN ('MARKET_SELECTED','NO_MARKET');
                               resolution_id TEXT NOT NULL UNIQUE
                                 CHECK(resolution_id != fill_evaluation_step_id);
                               resolved_at TEXT NOT NULL
market_observation_selections  PK market_observation_selection_id;
                               UNIQUE(fill_evaluation_step_id);
                               UNIQUE(fill_evaluation_plan_id, market_observation_id)
no_market_outcomes             PK no_market_outcome_id;
                               UNIQUE(fill_evaluation_step_id)
fills                          PK paper_fill_id;
                               UNIQUE(market_observation_selection_id)
positions                      PK paper_position_id;
                               position_contract_version TEXT NOT NULL;
                               paper_account_id REFERENCES account_bootstraps;
                               entry_paper_order_id REFERENCES orders;
                               pair TEXT NOT NULL;
                               position_side TEXT NOT NULL CHECK IN ('LONG','SHORT');
                               created_at TEXT NOT NULL;
                               written only by the first ENTRY application (T3a/T3b)
position_fill_applications     application_seq INTEGER PRIMARY KEY AUTOINCREMENT;
                               UNIQUE(paper_position_fill_application_id);
                               UNIQUE(paper_fill_id);
                               paper_position_id REFERENCES positions;
                               paper_order_id REFERENCES orders;
                               application_kind CHECK IN ('ENTRY','REDUCE_ONLY')
position_snapshots             PK paper_position_snapshot_id;
                               paper_account_id REFERENCES account_bootstraps
account_snapshots              PK paper_account_snapshot_id;
                               paper_account_id REFERENCES account_bootstraps
ledger_entries                 ledger_entry_seq INTEGER PRIMARY KEY AUTOINCREMENT;
                               UNIQUE(ledger_entry_id);
                               paper_account_id REFERENCES account_bootstraps;
                               UNIQUE(entry_kind, source_evidence_id)
swap_rollover_claims           paper_position_id REFERENCES positions;
                               rollover_date TEXT NOT NULL;
                               PRIMARY KEY (paper_position_id, rollover_date);
                               variant TEXT NOT NULL
                                 CHECK IN ('ACCRUED','NOT_ACCRUED');
                               evidence_id TEXT NOT NULL UNIQUE
                                 CHECK(evidence_id != paper_position_id);
                               resolved_at TEXT NOT NULL
swap_accruals                  PK paper_swap_accrual_id;
                               UNIQUE(paper_position_id, rollover_date)
swap_non_accruals              PK paper_swap_non_accrual_id;
                               UNIQUE(paper_position_id, rollover_date)
swap_accrual_corrections       correction_seq INTEGER PRIMARY KEY AUTOINCREMENT
                                 (insert-order audit only);
                               UNIQUE(correction_id);
                               corrected_accrual_id REFERENCES swap_accruals;
                               UNIQUE(corrected_accrual_id, chain_ordinal);
                               UNIQUE(predecessor_correction_id) so one chain cannot
                                 fork; deliberately not unique per accrual, because a
                                 chain holds many ordinals
reservation_consumptions       PK consumption_id; UNIQUE(paper_fill_id)
reservation_releases           PK release_id; UNIQUE(paper_order_id)
reconciliation_results         PK reconciliation_result_id
```

The Step-level cross-variant terminal claim mechanism is frozen as the single
`live_paper_step_terminal_claims` table with exactly four columns:
`fill_evaluation_step_id` (PRIMARY KEY, so one Step admits one claim across both
variants), `variant`, `resolution_id`, and audit `resolved_at`.

`resolution_id` is the identifier of the exact variant child record this claim
resolves: the `market_observation_selection_id` when `variant` is `MARKET_SELECTED`,
and the `no_market_outcome_id` when `variant` is `NO_MARKET`. It is never the Step ID,
never the plan ID, and never a value derived from the claim row itself; a CHECK
rejects `resolution_id = fill_evaluation_step_id`, and `UNIQUE(resolution_id)` then
proves one child record resolves at most one Step.

Two triggers make the linkage bidirectional and non-vacuous. Before inserting into
`market_observation_selections`, a claim must already exist for the same
`fill_evaluation_step_id` with `variant = 'MARKET_SELECTED'` and
`resolution_id = NEW.market_observation_selection_id`; before inserting into
`no_market_outcomes`, a claim must already exist for the same
`fill_evaluation_step_id` with `variant = 'NO_MARKET'` and
`resolution_id = NEW.no_market_outcome_id`. A child row whose ID does not equal its
claim's `resolution_id`, or whose claim carries the other variant, is rejected.
Per-variant uniqueness inside the two child tables is retained but is never the only
proof.

The rollover claim is structurally identical and is frozen the same way.
`live_paper_swap_rollover_claims` has exactly five columns: `paper_position_id`,
`rollover_date`, `variant`, `evidence_id`, and audit `resolved_at`, with
`PRIMARY KEY (paper_position_id, rollover_date)` so one position-and-date admits one
claim across both variants. `evidence_id` is the identifier of the exact variant child
record: the `paper_swap_accrual_id` when `variant` is `ACCRUED` and the
`paper_swap_non_accrual_id` when it is `NOT_ACCRUED`. A CHECK rejects
`evidence_id = paper_position_id` so `UNIQUE(evidence_id)` cannot be made vacuous.
Two triggers mirror the Step-claim pair: before inserting into `swap_accruals` a claim
must already exist for the same `(paper_position_id, rollover_date)` with
`variant = 'ACCRUED'` and `evidence_id = NEW.paper_swap_accrual_id`; before inserting
into `swap_non_accruals` a claim must already exist for the same pair with
`variant = 'NOT_ACCRUED'` and `evidence_id = NEW.paper_swap_non_accrual_id`. A direct
insert of an accrual for a date already claimed `NOT_ACCRUED`, or the reverse, is
therefore rejected at the schema boundary and not only by the write path.

Frozen minimum constraint set:

```text
one approved intent            -> one FillEvaluationPlan (via one PaperOrder)
one (plan_id, ordinal)         -> one FillEvaluationStep
one fill_evaluation_step_id    -> at most one terminal resolution claim
one fill_evaluation_step_id    -> at most one MarketObservationSelection
one selection_id               -> at most one PaperFill
```

PENDING attempts may be many and are individually immutable.

Transactions are per semantic unit and leave no partial evidence:

- T0 market observations: append-or-compare supplied observations.
- T1 order acceptance: fill policy, account bootstrap, `PaperOrder`, ordinal-0
  `ACCEPTED` event, `FillEvaluationPlan`.
- T2 Step creation: one `FillEvaluationStep`, plus the `OPEN` event for ordinal 0.
- T3 Step resolution: the terminal claim, plus exactly one of three branches. The
  branches are exhaustive; no other T3 shape exists.
  - T3a filled, order continues: selection, Fill, one `PARTIALLY_FILLED` or `FILLED`
    order event, the `live_paper_positions` row by append-or-compare when this is the
    first `ENTRY` application, the position fill application, a realized-PnL ledger
    entry for a reduce-only Fill, position snapshot, account snapshot, and for
    ordinary close one reservation consumption. This branch applies when the Fill
    leaves zero remainder (`FILLED`) or leaves a positive remainder that a further
    Step may still work (`ordinal + 1 < maximum_steps`).
  - T3b filled and terminal: identical to T3a, and additionally the order becomes
    terminal in the same transaction because a positive remainder cannot continue
    (`ordinal + 1 == maximum_steps`). The order events are appended as one ordered
    pair, `PARTIALLY_FILLED` first and then `incomplete_terminal_order_state`, at
    consecutive ordinals. For ordinary close, the reservation consumption is written
    first and exactly one reservation release of
    `intent.quantity - consumed_total` — where `consumed_total` already includes that
    consumption — is written after it. That release must be strictly positive.
  - T3c no market: no-market outcome, one terminal order event
    (`no_fill_terminal_order_state` when the order has zero Fills, otherwise
    `incomplete_terminal_order_state`), and for ordinary close one reservation
    release of the remainder.
- T4 PENDING attempt: one attempt row.
- T5 swap rollover, in this exact write order: the `(paper_position_id,
  rollover_date)` claim; then either one `PaperSwapAccrual` bound to the
  caller-supplied, in-transaction-authenticated pre-existing
  `paper_position_snapshot_id` plus one `SWAP_ACCRUAL` ledger entry, or one
  `PaperSwapNonAccrual` and no ledger entry; then, for the accrual case only, one new
  `PaperPositionSnapshot` and one new `PaperAccountSnapshot` at the boundaries in
  effect at the end of the transaction. A non-accrual writes no snapshot because no
  aggregate changed. The mark set for the account snapshot uses the no-Fill coverage
  rule, and every mark must already exist in `live_paper_market_observations` from an
  earlier committed T0; T5 writes no market observation, and a mark with no persisted
  row is a missing-parent integrity failure that rolls the transaction back.
- T6 swap accrual correction, in this exact write order: hydrate the exact
  `PaperSwapAccrual` and its complete existing correction chain ordered by
  `chain_ordinal`, require the supplied `chain_ordinal` to be exactly
  `len(chain) + 1` and the supplied `predecessor_correction_id` to be the chain's
  current last `correction_id` (or `None` for an empty chain), and require
  `effective_amount_before` and `delta_amount` to equal the recomputed values; then
  append one `PaperSwapAccrualCorrection`; then one `SWAP_ACCRUAL_CORRECTION` ledger
  entry; then one new `PaperPositionSnapshot` and one new `PaperAccountSnapshot` at
  the boundaries in effect at the end of the transaction, so `accrued_swap_total` and
  `equity` are persisted, not merely computable. The rollover claim is not touched.
  The mark set uses the no-Fill coverage rule, and every mark must already exist in
  `live_paper_market_observations` from an earlier committed T0; T6 writes no market
  observation, and a mark with no persisted row is a missing-parent integrity failure
  that rolls the transaction back.
- T7 reconciliation: one `PaperReconciliationResult`.

Each transaction uses `BEGIN IMMEDIATE`, authenticates every persisted parent by full
content before writing, and re-reads what it wrote before commit. Any conflict,
corruption, missing parent, or injected failure rolls back the entire transaction.

Retry hydrates the existing plan, Step, window, due boundary, resolution, selection,
and Fill and reuses them only on exact equality of every semantic field. It never
recomputes remaining quantity from current external state and never re-derives a
boundary from wall-clock time.

## B1 — Paper execution contracts and market evidence

Scope: the pure immutable Paper domain. Add `PaperIntentKind`,
`PaperOrderIntentLineage` with the three frozen source-intent payload builders and
the Paper position identity derivation, `PaperMarketObservation`, `PaperFillPolicy`,
`PaperPartialFillMode`, `PaperOrderState`, `PaperOrder`, `PaperOrderEvent`, the legal
transition table with `require_legal_transition()` and
`project_paper_order_state()`, `FillEvaluationPlan`, `FillEvaluationStep`,
`FillEvaluationAttempt`, `PaperAttemptDiagnosticCode`, `PaperStepResolutionVariant`,
`PaperMarketObservationSelection`, `PaperNoMarketOutcome`, `PaperFill`, and the two
frozen `Decimal` arithmetic contexts.

Expected implementation surface: new `apps/swap_bot/src/swap_bot/paper/__init__.py`
and `apps/swap_bot/src/swap_bot/paper/contracts.py`; focused tests under
`tests/paper_domain/`; new architecture tripwire cases for the `paper` package;
`docs/08_TEST_STRATEGY.md` and ExecPlan 0006 Progress.

B1 must not add: any store, SQL, migration, Clock use, market selection, fill
computation, ledger, PnL, swap accrual, reservation evidence, application service,
seed, randomness, or import of `fx_research`, `execution`, `ports`, `portfolio`,
`risk`, `shadow`, or `llm_feature`.

## B2 — Deterministic fill engine

Scope: the pure deterministic engine. Add the eligibility predicate, the
deterministic selection ordering with the earlier-Step exclusion set, the adverse
slippage fill-price formula, the versioned partial-fill quantity rule, the
remaining-quantity lineage, the next-Step derivation, and the PENDING/terminal
no-market branch with its `PaperAttemptDiagnosticCode` precedence, exposed as one
typed per-Step evaluation result carrying the proposed selection, Fill, attempt, or
no-market outcome plus the proposed order event or ordered pair of order events.

Expected implementation surface: new
`apps/swap_bot/src/swap_bot/paper/fill_engine.py`; additive exports in
`paper/__init__.py`; focused tests under `tests/paper_execution/`; tripwire case for
the new module; `docs/08_TEST_STRATEGY.md` and ExecPlan 0006 Progress.

B2 must not add: any store, SQL, migration, ledger, PnL, swap accrual, reservation
evidence, application service, automatic retry, sleep, backoff, randomness, or a
second market-selection path. It never reads a Clock; `evaluated_at` is supplied. It
does not hydrate or choose the candidate set: B4 owns the single frozen hydration
query, and B2's predicate and ordering operate on the tuple it is given.

## B3 — Paper ledger, position, account, PnL, and swap

Scope: the pure accounting domain. Add `PaperAccountBootstrap`,
`PaperPositionSide`, `PaperPositionApplicationKind`, `PaperPositionFillApplication`
with its content-addressed identity, the position projection and its
entry-after-reduce-only prohibition,
`PaperLedgerEntryKind`, `PaperLedgerEntry`, `PaperAccountMarkSet` with its coverage
rule, `PaperPositionSnapshot`, `PaperAccountSnapshot`, the seven named formulas with
their explicit versions, `PaperSwapAccrualPolicy`, the
`paper-swap-rollover-instant-v1` derivation, `PaperSwapAccrual`,
`PaperSwapNonAccrual`, `PaperSwapAccrualOutcome` with its frozen precedence,
`PaperSwapAccrualCorrection` with its chained-delta rule, and the reconciliation
contracts `PaperReconciliationResult`, `PaperReconciliationOutcome`, and
`PaperReconciledRecordKind` together with the pure rebuild-and-compare functions for
the four reconciled record kinds.

Expected implementation surface: new `apps/swap_bot/src/swap_bot/paper/ledger.py`;
additive exports in `paper/__init__.py`; focused tests under `tests/paper_ledger/`;
tripwire case for the new module; `docs/08_TEST_STRATEGY.md` and ExecPlan 0006
Progress.

B3 must not add: any store, SQL, migration, Clock, FX conversion, non-JPY
settlement, margin call, liquidation trigger, reservation evidence, repair path,
application service, or change to `OperationalSwapEvidence`, `PositionSnapshot`, or
`AccountSnapshot`. It never selects marks itself; the mark set is supplied.

## B4 — Atomic persistence, recovery, and reservation settlement

Scope: additive Live migration `0006_paper_execution_ledger.sql` and one SQLite
Paper store implementing the eight frozen transactions (T0 through T7, with the three
exhaustive T3 branches) with append-or-compare persistence, full parent
authentication, hydrate-and-compare retry, the cross-variant Step terminal claim and
its two child-linkage triggers, `ReservationConsumptionEvidence`,
`ReservationReleaseEvidence`, the conservation equation evaluated inside the writing
transaction, the swap-rollover and swap-correction transactions with their snapshot
regeneration, and the persistence of `PaperReconciliationResult`.

B4 also owns the single frozen market-observation hydration query that produces the
candidate set, authenticates the caller-supplied `paper_position_snapshot_id` bound by
a `PaperSwapAccrual` and the supplied correction `chain_ordinal`/
`predecessor_correction_id`, and resolves no record by latest-row, "most recent", or
implicit-current selection anywhere. The hydration query's `ORDER BY ... LIMIT 1` is
the frozen deterministic ordering, not an implicit current-row lookup.

T5, T6, and T7 are public B4 store methods rather than application-service entry
points, because they are not driven by an approved intent. Each takes its audit
instant as an explicit argument that B4 validates as an exact UTC `datetime` no
earlier than the greatest instant in the frozen T5/T6/T7 non-regression scan set in
"Frozen time source". They are the only three such operations M3 exposes.

The store takes the evaluation instant as an explicit argument and revalidates it
against persisted plan state before any Step work, validates `PaperAccountMarkSet`
coverage before any snapshot write, and hydrates the persisted M2-D
`ApprovedCloseIntent` row before any ordinary-close write.

Expected implementation surface: new
`apps/swap_bot/src/swap_bot/migrations/0006_paper_execution_ledger.sql`; new
`apps/swap_bot/src/swap_bot/paper/store.py`; additive exports in
`paper/__init__.py`; the updated exact migration-filename assertion in
`tests/architecture/test_import_boundaries.py`; focused tests under
`tests/paper_persistence/`; `docs/05_DATA_AND_VERSIONING.md`,
`docs/08_TEST_STRATEGY.md`, and ExecPlan 0006 Progress.

`paper/store.py` is the only Paper module permitted to import `sqlite3` or
`live_migrations`. B4 must not edit migrations `0001` through `0005`, must not add an
application service, must not add a Clock or read an ambient clock, and must not add
automatic retry, repair, `INSERT OR IGNORE` used as proof of equality, or any write
outside the frozen table list.

## B5 — One-intent Paper application composition

Scope: the `Clock` Protocol and one application service with exactly three explicit
entry points, one per exact approved-intent type, each processing exactly one intent
once. The service is constructed with the injected `Clock` and one non-blank exact
`str` `worker_identity`, and reads the `Clock` exactly once per call; no entry point
accepts a `datetime` or a worker identity argument.

The swap-rollover (T5), swap-correction (T6), and reconciliation (T7) operations are
public B4 store methods, not application-service entry points; M3 exposes them
directly and M4 composes them. B5 adds no operation for them.

```text
prevalidate exact source intent, Pair, quantity, and authority
-> LIVE raises before any gateway, store, clock, or market work
-> SHADOW_NOT_SUBMITTED returns a typed result and writes nothing
-> PAPER: read the Clock once; require exact UTC datetime
-> PAPER: authenticate/persist order + plan (T1)
-> reuse or create the current Step (T2)
-> persist supplied market observations (T0), then evaluate the persisted
   candidate set (B2)
-> PENDING attempt (T4) or terminal Step resolution (T3)
-> optional deterministic PaperFill, order lifecycle event, ledger application
-> ordinary close: exact reservation consumption, the typed release on a terminal
   CANCELLED/EXPIRED/REJECTED, or both in that order when one transaction both
   fills and terminates the order
-> typed application result
```

The result type reports `disposition` in
`{SHADOW_NOT_SUBMITTED, PAPER_STEP_PENDING, PAPER_STEP_RESOLVED}` plus the projected
order state, Step ordinal, and the optional Fill, consumption, and release IDs. A
`SHADOW_NOT_SUBMITTED` result carries no identifiers.

Expected implementation surface: new
`apps/swap_bot/src/swap_bot/paper/application.py`; additive exports in
`paper/__init__.py`; focused tests under `tests/paper_application/`; tripwire case
for the new module and the runtime zero-Broker probe; `docs/README.md`,
`docs/01_ARCHITECTURE.md`, `docs/04_SWAP_BOT.md`, `docs/06_REPOSITORY_STRUCTURE.md`,
`docs/08_TEST_STRATEGY.md`, and ExecPlan 0006 Progress.

B5 must not add: a loop that forces order completion, automatic sleep, backoff or
retry, Position discovery, batch or multi-intent processing, cycle input freezing,
scheduler, daemon, CLI, a fourth entry point, a union or converted intent parameter,
a caller-supplied evaluation or audit instant, a second Clock read per call, a
concrete production Clock implementation beyond one thin UTC adapter, or any new SQL,
migration, or persistence behaviour.
