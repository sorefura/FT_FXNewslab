import sqlite3
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fx_core import Currency
from swap_bot.models import CandidateId, ExecutionIntentId, RiskDecisionId
from swap_bot.paper import (
    PaperAccountBootstrap,
    PaperPartialFillMode,
    PaperPersistenceIntegrityError,
    PaperSwapAccrualPolicy,
    SQLitePaperStore,
    StepResolutionOutcome,
)
from swap_bot.strategy.swap_evidence import OperationalSwapEvidence
from swap_bot.swap import SwapAvailability

from tests.paper_persistence._helpers import (
    NOW,
    PAIR,
    bootstrap,
    execution_intent,
    fill_policy,
    observation,
)

_JPY = Currency("JPY")
LATER = NOW + timedelta(hours=1)
# Strictly between NOW and LATER, so it is rejected by whichever column carries LATER
# and would otherwise be accepted (every other column stays at or before NOW).
BETWEEN = NOW + timedelta(minutes=30)


def _swap_policy(**overrides: object) -> PaperSwapAccrualPolicy:
    values: dict[str, object] = {
        "policy_version": "swap-policy-v1",
        "unit_basis_base_units": (("STANDARD_LOT", Decimal("100000")),),
        "maximum_swap_age": timedelta(days=3),
        "settlement_currency": _JPY,
    }
    values.update(overrides)
    return PaperSwapAccrualPolicy.create(**values)  # type: ignore[arg-type]


def _evidence(**overrides: object) -> OperationalSwapEvidence:
    values: dict[str, object] = {
        "evidence_contract_version": "operational-swap-evidence-v1",
        "pair": PAIR,
        "availability": SwapAvailability.AVAILABLE,
        "long_received_amount": Decimal("120"),
        "short_received_amount": Decimal("-80"),
        "unit_basis": "STANDARD_LOT",
        "settlement_currency": _JPY,
        "source": "test",
        "source_version": "v1",
        "provider_observed_at": NOW,
        "received_at": NOW,
        "effective_from": NOW - timedelta(days=1),
        "effective_until": None,
    }
    values.update(overrides)
    return OperationalSwapEvidence.create(**values)  # type: ignore[arg-type]


def _open_entry_position(store: SQLitePaperStore, account, *, quantity: Decimal, mark, key: str):
    intent = execution_intent(
        intent_id=ExecutionIntentId(f"intent-{key}"),
        candidate_id=CandidateId(f"candidate-{key}"),
        risk_decision_id=RiskDecisionId(f"risk-{key}"),
        quantity=quantity,
        idempotency_key=f"idem-{key}",
    )
    accepted = store.accept_entry_order(
        fill_policy=fill_policy(maximum_steps=1),
        account_bootstrap=account,
        intent=intent,
        evaluated_at=NOW,
    )
    step = store.create_step(plan=accepted.plan, ordinal=0, evaluated_at=NOW)
    result = store.evaluate_step(
        step=step.step,
        plan=accepted.plan,
        worker_identity="w1",
        evaluated_at=NOW,
        mark_observations=(mark,),
    )
    assert result.outcome is StepResolutionOutcome.T3A
    return (
        accepted.order.intent_lineage.paper_position_id,
        result.position_snapshot.paper_position_snapshot_id,
    )


def _persist_bootstrap_only(path: Path, account: PaperAccountBootstrap) -> None:
    # live_paper_account_bootstraps carries no timestamp and no scan-set column, and
    # every public store method that persists it (T1) also appends an order event, so
    # this inserts it directly to leave the account's scan set genuinely empty.
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO live_paper_account_bootstraps "
            "(paper_account_id, bootstrap_contract_version, initial_cash, "
            "settlement_currency, margin_policy_version, leverage, "
            "unrealized_mark_policy_version) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                account.paper_account_id,
                account.bootstrap_contract_version,
                str(account.initial_cash),
                account.settlement_currency.code,
                account.margin_policy_version,
                str(account.leverage),
                account.unrealized_mark_policy_version,
            ),
        )


# ---------------------------------------------------------------------------
# Each of the seven frozen scan-set columns is independently falsifiable: for
# each, only that column carries the account's strictly greatest instant, and a
# T5/T6/T7 call earlier than it is rejected. Every test uses reconcile_account
# (T7) as the assertion call, since the scan set is identical across T5/T6/T7.
# ---------------------------------------------------------------------------


def test_scan_set_order_events_appended_at_bounds_the_floor(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    store = SQLitePaperStore(path)
    account = bootstrap()
    intent = execution_intent(
        intent_id=ExecutionIntentId("intent-oe"),
        candidate_id=CandidateId("candidate-oe"),
        risk_decision_id=RiskDecisionId("risk-oe"),
        quantity=Decimal("100"),
        idempotency_key="idem-oe",
    )
    # T1 order acceptance writes only orders/plans/fill-policy/bootstrap plus the
    # ordinal-0 ACCEPTED order event; no Step exists yet, so only order_events is
    # touched at LATER.
    store.accept_entry_order(
        fill_policy=fill_policy(),
        account_bootstrap=account,
        intent=intent,
        evaluated_at=LATER,
    )
    with pytest.raises(PaperPersistenceIntegrityError):
        store.reconcile_account(paper_account_id=account.paper_account_id, resolved_at=BETWEEN)


def test_scan_set_fill_evaluation_steps_created_at_bounds_the_floor(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    store = SQLitePaperStore(path)
    account = bootstrap()
    mark = observation()
    store.append_market_observations((mark,))
    intent = execution_intent(
        intent_id=ExecutionIntentId("intent-step"),
        candidate_id=CandidateId("candidate-step"),
        risk_decision_id=RiskDecisionId("risk-step"),
        quantity=Decimal("1000"),
        idempotency_key="idem-step",
    )
    policy = fill_policy(
        maximum_steps=2,
        partial_fill_mode=PaperPartialFillMode.FRACTION_OF_REMAINING,
        partial_fill_fraction=Decimal("0.3"),
    )
    accepted = store.accept_entry_order(
        fill_policy=policy, account_bootstrap=account, intent=intent, evaluated_at=NOW
    )
    step0 = store.create_step(plan=accepted.plan, ordinal=0, evaluated_at=NOW)
    result0 = store.evaluate_step(
        step=step0.step,
        plan=accepted.plan,
        worker_identity="w1",
        evaluated_at=NOW,
        mark_observations=(mark,),
    )
    assert result0.outcome is StepResolutionOutcome.T3A
    # Ordinal 1 writes only the Step row (ordinal 0 already owns the OPEN event), so
    # this isolates fill_evaluation_steps.created_at at LATER without also moving
    # order_events.appended_at.
    store.create_step(plan=accepted.plan, ordinal=1, evaluated_at=LATER)
    with pytest.raises(PaperPersistenceIntegrityError):
        store.reconcile_account(paper_account_id=account.paper_account_id, resolved_at=BETWEEN)


def test_scan_set_fill_evaluation_attempts_evaluated_at_bounds_the_floor(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    store = SQLitePaperStore(path)
    account = bootstrap()
    intent = execution_intent(
        intent_id=ExecutionIntentId("intent-attempt"),
        candidate_id=CandidateId("candidate-attempt"),
        risk_decision_id=RiskDecisionId("risk-attempt"),
        quantity=Decimal("100"),
        idempotency_key="idem-attempt",
    )
    # A wide window so evaluating at LATER (well before evaluation_due_at) with zero
    # persisted observations yields PENDING rather than a terminal NO_MARKET outcome.
    policy = fill_policy(step_window_duration=timedelta(hours=6))
    accepted = store.accept_entry_order(
        fill_policy=policy, account_bootstrap=account, intent=intent, evaluated_at=NOW
    )
    step = store.create_step(plan=accepted.plan, ordinal=0, evaluated_at=NOW)
    result = store.evaluate_step(
        step=step.step, plan=accepted.plan, worker_identity="w1", evaluated_at=LATER
    )
    assert result.outcome is StepResolutionOutcome.PENDING
    with pytest.raises(PaperPersistenceIntegrityError):
        store.reconcile_account(paper_account_id=account.paper_account_id, resolved_at=BETWEEN)


def test_scan_set_swap_accruals_created_at_bounds_the_floor(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    store = SQLitePaperStore(path)
    account = bootstrap()
    mark = observation()
    store.append_market_observations((mark,))
    _position_id, snapshot_id = _open_entry_position(
        store, account, quantity=Decimal("1000"), mark=mark, key="a"
    )
    store.accrue_or_skip_swap(
        paper_position_snapshot_id=snapshot_id,
        evidence=_evidence(),
        rollover_date=date(2026, 1, 2),
        policy=_swap_policy(),
        mark_observations=(mark,),
        resolved_at=LATER,
    )
    with pytest.raises(PaperPersistenceIntegrityError):
        store.reconcile_account(paper_account_id=account.paper_account_id, resolved_at=BETWEEN)


def test_scan_set_swap_non_accruals_created_at_bounds_the_floor(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    store = SQLitePaperStore(path)
    account = bootstrap()
    mark = observation()
    store.append_market_observations((mark,))
    _position_id, snapshot_id = _open_entry_position(
        store, account, quantity=Decimal("1000"), mark=mark, key="a"
    )
    store.accrue_or_skip_swap(
        paper_position_snapshot_id=snapshot_id,
        evidence=None,
        rollover_date=date(2026, 1, 2),
        policy=_swap_policy(),
        mark_observations=(),
        resolved_at=LATER,
    )
    with pytest.raises(PaperPersistenceIntegrityError):
        store.reconcile_account(paper_account_id=account.paper_account_id, resolved_at=BETWEEN)


def test_scan_set_swap_accrual_corrections_created_at_bounds_the_floor(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    store = SQLitePaperStore(path)
    account = bootstrap()
    mark = observation()
    store.append_market_observations((mark,))
    _position_id, snapshot_id = _open_entry_position(
        store, account, quantity=Decimal("1000"), mark=mark, key="a"
    )
    accrual_earlier = NOW + timedelta(minutes=5)
    assert accrual_earlier < BETWEEN
    accrual_result = store.accrue_or_skip_swap(
        paper_position_snapshot_id=snapshot_id,
        evidence=_evidence(),
        rollover_date=date(2026, 1, 2),
        policy=_swap_policy(),
        mark_observations=(mark,),
        resolved_at=accrual_earlier,
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
        resolved_at=LATER,
    )
    with pytest.raises(PaperPersistenceIntegrityError):
        store.reconcile_account(paper_account_id=account.paper_account_id, resolved_at=BETWEEN)


def test_scan_set_reconciliation_results_created_at_bounds_the_floor(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    store = SQLitePaperStore(path)
    account = bootstrap()
    _persist_bootstrap_only(path, account)
    store.reconcile_account(paper_account_id=account.paper_account_id, resolved_at=LATER)
    with pytest.raises(PaperPersistenceIntegrityError):
        store.reconcile_account(paper_account_id=account.paper_account_id, resolved_at=BETWEEN)


def test_scan_set_is_account_scoped_and_ignores_a_later_row_of_a_different_account(
    tmp_path: Path,
) -> None:
    path = tmp_path / "live.sqlite"
    store = SQLitePaperStore(path)
    account_a = bootstrap(initial_cash=Decimal("1000000"))
    account_b = bootstrap(initial_cash=Decimal("2000000"))
    _persist_bootstrap_only(path, account_a)
    _persist_bootstrap_only(path, account_b)
    store.reconcile_account(paper_account_id=account_b.paper_account_id, resolved_at=LATER)
    # Account A has no activity at all, so account B's later row imposes no bound.
    result = store.reconcile_account(
        paper_account_id=account_a.paper_account_id, resolved_at=BETWEEN
    )
    assert result is not None


def test_scan_set_empty_only_requires_utc_exactness(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    store = SQLitePaperStore(path)
    account = bootstrap()
    _persist_bootstrap_only(path, account)
    result = store.reconcile_account(
        paper_account_id=account.paper_account_id,
        resolved_at=datetime(2000, 1, 1, tzinfo=UTC),
    )
    assert result is not None
