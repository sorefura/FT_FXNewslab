"""Shared builders for tests/paper_persistence (not a test module itself)."""

import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from fx_core import Currency, CurrencyPair
from swap_bot.adoption import digest
from swap_bot.execution_authority import ExecutionAuthorityMode
from swap_bot.models import (
    ApprovedExecutionIntent,
    ApprovedLiquidationIntent,
    CandidateId,
    ExecutionIntentId,
    PositionId,
    RiskDecisionId,
    Side,
)
from swap_bot.paper import (
    AcceptedOrder,
    EvaluatedStep,
    PaperAccountBootstrap,
    PaperFillPolicy,
    PaperMarketObservation,
    PaperOrderState,
    PaperPartialFillMode,
    PaperSwapAccrualPolicy,
    SQLitePaperStore,
)
from swap_bot.strategy.ordinary_close import ApprovedCloseIntent, _intent_payload
from swap_bot.strategy.swap_evidence import OperationalSwapEvidence
from swap_bot.swap import SwapAvailability

PAIR = CurrencyPair.parse("USD_JPY")
OTHER_PAIR = CurrencyPair.parse("MXN_JPY")
NOW = datetime(2026, 1, 1, tzinfo=UTC)
JPY = Currency("JPY")


def bootstrap(**overrides: object) -> PaperAccountBootstrap:
    values: dict[str, object] = {
        "initial_cash": Decimal("1000000"),
        "settlement_currency": JPY,
        "margin_policy_version": "margin-v1",
        "leverage": Decimal("25"),
        "unrealized_mark_policy_version": "mark-v1",
    }
    values.update(overrides)
    return PaperAccountBootstrap.create(**values)  # type: ignore[arg-type]


def fill_policy(**overrides: object) -> PaperFillPolicy:
    values: dict[str, object] = {
        "policy_version": "policy-v1",
        "market_selection_policy_version": "selection-v1",
        "fill_model_version": "fill-v1",
        "step_schedule_policy_version": "schedule-v1",
        "maximum_market_age": timedelta(minutes=5),
        "step_window_duration": timedelta(minutes=1),
        "step_gap": timedelta(seconds=1),
        "maximum_steps": 3,
        "partial_fill_mode": PaperPartialFillMode.FULL_REMAINING,
        "partial_fill_fraction": None,
        "slippage_basis_points": Decimal("0"),
        "no_fill_terminal_order_state": PaperOrderState.REJECTED,
        "incomplete_terminal_order_state": PaperOrderState.CANCELLED,
    }
    values.update(overrides)
    return PaperFillPolicy.create(**values)  # type: ignore[arg-type]


def execution_intent(**overrides: object) -> ApprovedExecutionIntent:
    values: dict[str, object] = {
        "intent_id": ExecutionIntentId("intent-1"),
        "candidate_id": CandidateId("candidate-1"),
        "risk_decision_id": RiskDecisionId("risk-1"),
        "pair": PAIR,
        "side": Side.BUY,
        "quantity": Decimal("1000"),
        "idempotency_key": "entry-idem-1",
        "created_at": NOW,
    }
    values.update(overrides)
    return ApprovedExecutionIntent(**values)  # type: ignore[arg-type]


def liquidation_intent(**overrides: object) -> ApprovedLiquidationIntent:
    values: dict[str, object] = {
        "intent_id": ExecutionIntentId("liquidation-intent-1"),
        "risk_decision_id": RiskDecisionId("risk-2"),
        "position_id": PositionId("position-1"),
        "pair": PAIR,
        "quantity": Decimal("500"),
        "idempotency_key": "liquidation-idem-1",
        "created_at": NOW,
    }
    values.update(overrides)
    return ApprovedLiquidationIntent(**values)  # type: ignore[arg-type]


def observation(**overrides: object) -> PaperMarketObservation:
    values: dict[str, object] = {
        "pair": PAIR,
        "bid": Decimal("150.000"),
        "ask": Decimal("150.005"),
        "provider_observed_at": NOW,
        "received_at": NOW,
        "source": "test-provider",
        "source_version": "v1",
    }
    values.update(overrides)
    return PaperMarketObservation.create(**values)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Synthetic M2-D ordinary-close reservation evidence.
#
# B4 only authenticates the persisted live_ordinary_close_approved_intents row by
# full content; it never re-derives M2-D's Portfolio/Risk evaluation. Building the
# complete real M2-D evaluation chain (Signal/Adoption/Portfolio/Risk) for every
# reservation-settlement test would duplicate that milestone's own coverage, so
# these tests insert a schema-valid Intent row directly (content-addressed exactly
# like the real one) and exercise B4's authentication against it.
# ---------------------------------------------------------------------------


def synthetic_close_intent(
    *,
    position_id: PositionId,
    pair: CurrencyPair = PAIR,
    side: Side = Side.SELL,
    quantity: Decimal,
    authority: ExecutionAuthorityMode = ExecutionAuthorityMode.PAPER,
    created_at: datetime = NOW,
    close_candidate_id: str = "close-candidate-1",
    portfolio_decision_id: str = "portfolio-decision-1",
    risk_decision_id: str = "risk-decision-close-1",
    capacity_evidence_id: str = "capacity-evidence-1",
) -> ApprovedCloseIntent:
    payload = _intent_payload(
        close_candidate_id=close_candidate_id,
        portfolio_decision_id=portfolio_decision_id,
        risk_decision_id=risk_decision_id,
        capacity_evidence_id=capacity_evidence_id,
        position_id=position_id,
        pair=pair,
        side=side,
        quantity=quantity,
    )
    idempotency_key = "approved-close-intent-" + digest(payload)
    return ApprovedCloseIntent(
        close_candidate_id,
        portfolio_decision_id,
        risk_decision_id,
        capacity_evidence_id,
        position_id,
        pair,
        side,
        quantity,
        authority,
        idempotency_key,
        created_at,
    )


def insert_m2d_intent(path: str | Path, intent: ApprovedCloseIntent) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO live_ordinary_close_risk_decisions "
            "(risk_decision_id, portfolio_decision_id, risk_policy_version, "
            "maximum_capacity_age_us, outcome, reason) VALUES (?, ?, ?, ?, 'APPROVE', 'APPROVED')",
            (intent.risk_decision_id, intent.portfolio_decision_id, "risk-v1", 3_600_000_000),
        )
        connection.execute(
            "INSERT INTO live_ordinary_close_approved_intents "
            "(close_candidate_id, portfolio_decision_id, risk_decision_id, capacity_evidence_id, "
            "position_id, pair, side, quantity, authority, idempotency_key, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                intent.close_candidate_id,
                intent.portfolio_decision_id,
                intent.risk_decision_id,
                intent.capacity_evidence_id,
                intent.position_id.value,
                intent.pair.symbol,
                intent.side.value,
                str(intent.quantity),
                intent.authority.value,
                intent.idempotency_key,
                intent.created_at.isoformat(),
            ),
        )


# ---------------------------------------------------------------------------
# A full, exercised flow touching every one of the 24 live_paper_* tables at
# least once: entry + full liquidation (REALIZED_PNL, swap accrual/correction),
# entry + partial ordinary close (T3b consumption+release), a NO_MARKET terminal
# resolution, a PENDING attempt, and one MATCHED reconciliation.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FullFlow:
    store: SQLitePaperStore
    path: Path
    bootstrap: PaperAccountBootstrap
    observation: PaperMarketObservation
    later: datetime
    position_id_1: str
    position_id_2: str
    entry1: AcceptedOrder
    liquidation1: AcceptedOrder
    result1: EvaluatedStep
    result_liquidation1: EvaluatedStep
    entry2: AcceptedOrder
    result2: EvaluatedStep
    close_intent: ApprovedCloseIntent
    accepted_close: AcceptedOrder
    result_close: EvaluatedStep


def populate_full_flow(path: Path) -> FullFlow:
    store = SQLitePaperStore(path)
    account_bootstrap = bootstrap()
    policy_full = fill_policy(maximum_steps=1)
    mark = observation()
    store.append_market_observations((mark,))

    entry1_intent = execution_intent(
        intent_id=ExecutionIntentId("intent-1"),
        candidate_id=CandidateId("candidate-1"),
        risk_decision_id=RiskDecisionId("risk-1"),
        quantity=Decimal("1000"),
        idempotency_key="entry-idem-1",
    )
    accepted1 = store.accept_entry_order(
        fill_policy=policy_full,
        account_bootstrap=account_bootstrap,
        intent=entry1_intent,
        evaluated_at=NOW,
    )
    step1 = store.create_step(plan=accepted1.plan, ordinal=0, evaluated_at=NOW)
    result1 = store.evaluate_step(
        step=step1.step,
        plan=accepted1.plan,
        worker_identity="w1",
        evaluated_at=NOW,
        mark_observations=(mark,),
    )
    position_id_1 = accepted1.order.intent_lineage.paper_position_id

    liq1_intent = liquidation_intent(
        intent_id=ExecutionIntentId("liq-1"),
        risk_decision_id=RiskDecisionId("risk-liq-1"),
        position_id=PositionId(position_id_1),
        quantity=Decimal("1000"),
        idempotency_key="liq-idem-1",
    )
    accepted_liq1 = store.accept_emergency_liquidation_order(
        fill_policy=policy_full,
        account_bootstrap=account_bootstrap,
        intent=liq1_intent,
        existing_position_side=Side.BUY,
        evaluated_at=NOW,
    )
    step_liq1 = store.create_step(plan=accepted_liq1.plan, ordinal=0, evaluated_at=NOW)
    result_liq1 = store.evaluate_step(
        step=step_liq1.step,
        plan=accepted_liq1.plan,
        worker_identity="w1",
        evaluated_at=NOW,
        mark_observations=(mark,),
    )

    entry2_intent = execution_intent(
        intent_id=ExecutionIntentId("intent-2"),
        candidate_id=CandidateId("candidate-2"),
        risk_decision_id=RiskDecisionId("risk-2"),
        quantity=Decimal("500"),
        idempotency_key="entry-idem-2",
    )
    accepted2 = store.accept_entry_order(
        fill_policy=policy_full,
        account_bootstrap=account_bootstrap,
        intent=entry2_intent,
        evaluated_at=NOW,
    )
    step2 = store.create_step(plan=accepted2.plan, ordinal=0, evaluated_at=NOW)
    result2 = store.evaluate_step(
        step=step2.step,
        plan=accepted2.plan,
        worker_identity="w1",
        evaluated_at=NOW,
        mark_observations=(mark,),
    )
    position_id_2 = accepted2.order.intent_lineage.paper_position_id

    # A distinct Pair with zero persisted observations, so evaluating at/after due
    # first-writes a terminal NO_MARKET claim.
    no_obs_pair = CurrencyPair.parse("EUR_JPY")
    entry3_intent = execution_intent(
        intent_id=ExecutionIntentId("intent-3"),
        candidate_id=CandidateId("candidate-3"),
        risk_decision_id=RiskDecisionId("risk-3"),
        pair=no_obs_pair,
        side=Side.SELL,
        quantity=Decimal("100"),
        idempotency_key="entry-idem-3",
    )
    accepted3 = store.accept_entry_order(
        fill_policy=policy_full,
        account_bootstrap=account_bootstrap,
        intent=entry3_intent,
        evaluated_at=NOW,
    )
    step3 = store.create_step(plan=accepted3.plan, ordinal=0, evaluated_at=NOW)
    store.evaluate_step(
        step=step3.step,
        plan=accepted3.plan,
        worker_identity="w1",
        evaluated_at=step3.step.evaluation_due_at + timedelta(seconds=1),
    )

    # A distinct Pair with zero persisted observations, evaluated before due, so
    # it appends one PENDING attempt and leaves the Step unresolved.
    pending_pair = CurrencyPair.parse("GBP_JPY")
    entry4_intent = execution_intent(
        intent_id=ExecutionIntentId("intent-4"),
        candidate_id=CandidateId("candidate-4"),
        risk_decision_id=RiskDecisionId("risk-4"),
        pair=pending_pair,
        quantity=Decimal("100"),
        idempotency_key="entry-idem-4",
    )
    accepted4 = store.accept_entry_order(
        fill_policy=policy_full,
        account_bootstrap=account_bootstrap,
        intent=entry4_intent,
        evaluated_at=NOW,
    )
    step4 = store.create_step(plan=accepted4.plan, ordinal=0, evaluated_at=NOW)
    store.evaluate_step(
        step=step4.step, plan=accepted4.plan, worker_identity="w1", evaluated_at=NOW
    )

    close_intent = synthetic_close_intent(
        position_id=PositionId(position_id_2),
        quantity=Decimal("500"),
    )
    insert_m2d_intent(path, close_intent)
    close_policy = fill_policy(
        policy_version="policy-close-v1",
        maximum_steps=1,
        partial_fill_mode=PaperPartialFillMode.FRACTION_OF_REMAINING,
        partial_fill_fraction=Decimal("0.5"),
    )
    accepted_close = store.accept_ordinary_close_order(
        fill_policy=close_policy,
        account_bootstrap=account_bootstrap,
        intent=close_intent,
        evaluated_at=NOW,
    )
    step_close = store.create_step(plan=accepted_close.plan, ordinal=0, evaluated_at=NOW)
    result_close = store.evaluate_step(
        step=step_close.step,
        plan=accepted_close.plan,
        worker_identity="w1",
        evaluated_at=NOW,
        mark_observations=(mark,),
    )

    later = NOW + timedelta(minutes=5)
    swap_policy = PaperSwapAccrualPolicy.create(
        policy_version="swap-policy-v1",
        unit_basis_base_units=(("STANDARD_LOT", Decimal("100000")),),
        maximum_swap_age=timedelta(days=3),
        settlement_currency=JPY,
    )
    evidence = OperationalSwapEvidence.create(
        evidence_contract_version="operational-swap-evidence-v1",
        pair=PAIR,
        availability=SwapAvailability.AVAILABLE,
        long_received_amount=Decimal("120"),
        short_received_amount=Decimal("-80"),
        unit_basis="STANDARD_LOT",
        settlement_currency=JPY,
        source="test",
        source_version="v1",
        provider_observed_at=NOW,
        received_at=NOW,
        effective_from=NOW - timedelta(days=1),
        effective_until=None,
    )
    accrual_result = store.accrue_or_skip_swap(
        paper_position_snapshot_id=result_close.position_snapshot.paper_position_snapshot_id,
        evidence=evidence,
        rollover_date=date(2026, 1, 2),
        policy=swap_policy,
        mark_observations=(mark,),
        resolved_at=later,
    )
    store.accrue_or_skip_swap(
        paper_position_snapshot_id=result_liq1.position_snapshot.paper_position_snapshot_id,
        evidence=None,
        rollover_date=date(2026, 1, 2),
        policy=swap_policy,
        mark_observations=(),
        resolved_at=later,
    )
    assert accrual_result.accrual is not None
    store.correct_swap_accrual(
        corrected_accrual_id=accrual_result.accrual.paper_swap_accrual_id,
        chain_ordinal=1,
        predecessor_correction_id=None,
        replacement_amount=Decimal("50"),
        correction_reason="late evidence",
        swap_evidence_id="swap-evidence-correction-1",
        mark_observations=(mark,),
        resolved_at=later,
    )
    store.reconcile_account(paper_account_id=account_bootstrap.paper_account_id, resolved_at=later)

    return FullFlow(
        store=store,
        path=Path(path),
        bootstrap=account_bootstrap,
        observation=mark,
        later=later,
        position_id_1=position_id_1,
        position_id_2=position_id_2,
        entry1=accepted1,
        liquidation1=accepted_liq1,
        result1=result1,
        result_liquidation1=result_liq1,
        entry2=accepted2,
        result2=result2,
        close_intent=close_intent,
        accepted_close=accepted_close,
        result_close=result_close,
    )
