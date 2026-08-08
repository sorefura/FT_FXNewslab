import sqlite3
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fx_core import Currency
from swap_bot.models import CandidateId, ExecutionIntentId, RiskDecisionId
from swap_bot.paper import (
    PaperLedgerIntegrityError,
    PaperPersistenceIntegrityError,
    PaperReconciledRecordKind,
    PaperReconciliationOutcome,
    PaperSwapAccrualOutcome,
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


LATER = NOW + timedelta(minutes=5)


def test_accrual_and_missing_evidence_non_accrual(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    store = SQLitePaperStore(path)
    account = bootstrap()
    mark = observation()
    store.append_market_observations((mark,))
    position_id, snapshot_id = _open_entry_position(
        store, account, quantity=Decimal("1000"), mark=mark, key="a"
    )

    accrued = store.accrue_or_skip_swap(
        paper_position_snapshot_id=snapshot_id,
        evidence=_evidence(),
        rollover_date=date(2026, 1, 2),
        policy=_swap_policy(),
        mark_observations=(mark,),
        resolved_at=LATER,
    )
    assert accrued.outcome is PaperSwapAccrualOutcome.ACCRUED
    assert accrued.accrual is not None
    assert accrued.ledger_entry is not None
    assert accrued.position_snapshot is not None
    assert accrued.account_snapshot is not None

    position_id2, snapshot_id2 = _open_entry_position(
        store, account, quantity=Decimal("500"), mark=mark, key="b"
    )
    missing = store.accrue_or_skip_swap(
        paper_position_snapshot_id=snapshot_id2,
        evidence=None,
        rollover_date=date(2026, 1, 2),
        policy=_swap_policy(),
        mark_observations=(),
        resolved_at=LATER,
    )
    assert missing.outcome is PaperSwapAccrualOutcome.NOT_ACCRUED_SWAP_MISSING
    assert missing.non_accrual is not None
    assert missing.ledger_entry is None
    assert missing.position_snapshot is None


def test_accrual_rejects_a_mark_with_no_persisted_market_observation(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    store = SQLitePaperStore(path)
    account = bootstrap()
    mark = observation()
    store.append_market_observations((mark,))
    _position_id, snapshot_id = _open_entry_position(
        store, account, quantity=Decimal("1000"), mark=mark, key="a"
    )
    # Never persisted via T0.
    phantom_mark = observation(source="phantom-source")

    def _counts(connection: sqlite3.Connection) -> tuple[int, ...]:
        return tuple(
            connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "live_paper_swap_rollover_claims",
                "live_paper_swap_accruals",
                "live_paper_swap_non_accruals",
                "live_paper_ledger_entries",
                "live_paper_position_snapshots",
                "live_paper_account_snapshots",
            )
        )

    with sqlite3.connect(path) as connection:
        before = _counts(connection)
    with pytest.raises(PaperPersistenceIntegrityError):
        store.accrue_or_skip_swap(
            paper_position_snapshot_id=snapshot_id,
            evidence=_evidence(),
            rollover_date=date(2026, 1, 2),
            policy=_swap_policy(),
            mark_observations=(phantom_mark,),
            resolved_at=LATER,
        )
    with sqlite3.connect(path) as connection:
        after = _counts(connection)
    assert after == before


def test_correction_rejects_a_mark_with_no_persisted_market_observation(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    store = SQLitePaperStore(path)
    account = bootstrap()
    mark = observation()
    store.append_market_observations((mark,))
    _position_id, snapshot_id = _open_entry_position(
        store, account, quantity=Decimal("1000"), mark=mark, key="a"
    )
    accrual_result = store.accrue_or_skip_swap(
        paper_position_snapshot_id=snapshot_id,
        evidence=_evidence(),
        rollover_date=date(2026, 1, 2),
        policy=_swap_policy(),
        mark_observations=(mark,),
        resolved_at=LATER,
    )
    assert accrual_result.accrual is not None
    # Never persisted via T0.
    phantom_mark = observation(source="phantom-source")

    def _counts(connection: sqlite3.Connection) -> tuple[int, ...]:
        return tuple(
            connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "live_paper_swap_accrual_corrections",
                "live_paper_ledger_entries",
                "live_paper_position_snapshots",
                "live_paper_account_snapshots",
            )
        )

    with sqlite3.connect(path) as connection:
        before = _counts(connection)
    with pytest.raises(PaperPersistenceIntegrityError):
        store.correct_swap_accrual(
            corrected_accrual_id=accrual_result.accrual.paper_swap_accrual_id,
            chain_ordinal=1,
            predecessor_correction_id=None,
            replacement_amount=Decimal("50"),
            correction_reason="late evidence",
            swap_evidence_id="swap-evidence-correction-1",
            mark_observations=(phantom_mark,),
            resolved_at=LATER + timedelta(minutes=1),
        )
    with sqlite3.connect(path) as connection:
        after = _counts(connection)
    assert after == before


def test_accrual_snapshot_binding_rejects_wrong_position_and_superseded_snapshot(
    tmp_path: Path,
) -> None:
    path = tmp_path / "live.sqlite"
    store = SQLitePaperStore(path)
    account = bootstrap()
    mark = observation()
    store.append_market_observations((mark,))
    position_id_a, snapshot_id_a = _open_entry_position(
        store, account, quantity=Decimal("1000"), mark=mark, key="a"
    )

    # A superseded snapshot: apply a second Fill on position A, then try to accrue
    # against the pre-Fill snapshot.
    second_mark = observation(pair=PAIR, received_at=NOW, provider_observed_at=NOW)
    liquidation_intent_quantity = Decimal("1000")
    from swap_bot.models import ApprovedLiquidationIntent, PositionId, Side

    liq_intent = ApprovedLiquidationIntent(
        ExecutionIntentId("liq-a"),
        RiskDecisionId("risk-liq-a"),
        PositionId(position_id_a),
        PAIR,
        liquidation_intent_quantity,
        "liq-idem-a",
        NOW,
    )
    accepted_liq = store.accept_emergency_liquidation_order(
        fill_policy=fill_policy(maximum_steps=1),
        account_bootstrap=account,
        intent=liq_intent,
        existing_position_side=Side.BUY,
        evaluated_at=NOW,
    )
    step = store.create_step(plan=accepted_liq.plan, ordinal=0, evaluated_at=NOW)
    store.evaluate_step(
        step=step.step,
        plan=accepted_liq.plan,
        worker_identity="w1",
        evaluated_at=NOW,
        mark_observations=(second_mark,),
    )

    with pytest.raises(PaperPersistenceIntegrityError):
        store.accrue_or_skip_swap(
            paper_position_snapshot_id=snapshot_id_a,
            evidence=_evidence(),
            rollover_date=date(2026, 1, 2),
            policy=_swap_policy(),
            mark_observations=(),
            resolved_at=LATER,
        )


def test_chained_corrections_converge_to_the_last_replacement_amount(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    store = SQLitePaperStore(path)
    account = bootstrap()
    mark = observation()
    store.append_market_observations((mark,))
    position_id, snapshot_id = _open_entry_position(
        store, account, quantity=Decimal("1000"), mark=mark, key="a"
    )

    evidence = _evidence(long_received_amount=Decimal("100"))
    accrual_result = store.accrue_or_skip_swap(
        paper_position_snapshot_id=snapshot_id,
        evidence=evidence,
        rollover_date=date(2026, 1, 2),
        policy=_swap_policy(unit_basis_base_units=(("STANDARD_LOT", Decimal("1000")),)),
        mark_observations=(mark,),
        resolved_at=LATER,
    )
    assert accrual_result.accrual is not None
    assert accrual_result.accrual.amount == Decimal("100")

    first = store.correct_swap_accrual(
        corrected_accrual_id=accrual_result.accrual.paper_swap_accrual_id,
        chain_ordinal=1,
        predecessor_correction_id=None,
        replacement_amount=Decimal("120"),
        correction_reason="update-1",
        swap_evidence_id="evidence-1",
        mark_observations=(mark,),
        resolved_at=LATER,
    )
    assert first.correction.delta_amount == Decimal("20")

    second = store.correct_swap_accrual(
        corrected_accrual_id=accrual_result.accrual.paper_swap_accrual_id,
        chain_ordinal=2,
        predecessor_correction_id=first.correction.correction_id,
        replacement_amount=Decimal("130"),
        correction_reason="update-2",
        swap_evidence_id="evidence-2",
        mark_observations=(mark,),
        resolved_at=LATER,
    )
    assert second.correction.delta_amount == Decimal("10")
    assert second.position_snapshot.accrued_swap_total == Decimal("130")

    with sqlite3.connect(path) as connection:
        amounts = [
            Decimal(row[0])
            for row in connection.execute(
                "SELECT amount FROM live_paper_ledger_entries "
                "WHERE entry_kind IN ('SWAP_ACCRUAL', 'SWAP_ACCRUAL_CORRECTION') "
                "ORDER BY ledger_entry_seq ASC"
            )
        ]
    assert amounts == [Decimal("100"), Decimal("20"), Decimal("10")]


def test_oscillating_correction_chain_converges_and_never_collides(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    store = SQLitePaperStore(path)
    account = bootstrap()
    mark = observation()
    store.append_market_observations((mark,))
    position_id, snapshot_id = _open_entry_position(
        store, account, quantity=Decimal("1000"), mark=mark, key="a"
    )

    accrual_result = store.accrue_or_skip_swap(
        paper_position_snapshot_id=snapshot_id,
        evidence=_evidence(long_received_amount=Decimal("100")),
        rollover_date=date(2026, 1, 2),
        policy=_swap_policy(unit_basis_base_units=(("STANDARD_LOT", Decimal("1000")),)),
        mark_observations=(mark,),
        resolved_at=LATER,
    )
    accrual_id = accrual_result.accrual.paper_swap_accrual_id

    first = store.correct_swap_accrual(
        corrected_accrual_id=accrual_id,
        chain_ordinal=1,
        predecessor_correction_id=None,
        replacement_amount=Decimal("120"),
        correction_reason="up",
        swap_evidence_id="ev-1",
        mark_observations=(mark,),
        resolved_at=LATER,
    )
    second = store.correct_swap_accrual(
        corrected_accrual_id=accrual_id,
        chain_ordinal=2,
        predecessor_correction_id=first.correction.correction_id,
        replacement_amount=Decimal("100"),
        correction_reason="down",
        swap_evidence_id="ev-2",
        mark_observations=(mark,),
        resolved_at=LATER,
    )
    third = store.correct_swap_accrual(
        corrected_accrual_id=accrual_id,
        chain_ordinal=3,
        predecessor_correction_id=second.correction.correction_id,
        replacement_amount=Decimal("120"),
        correction_reason="up-again",
        swap_evidence_id="ev-3",
        mark_observations=(mark,),
        resolved_at=LATER,
    )
    assert {
        first.correction.correction_id,
        second.correction.correction_id,
        third.correction.correction_id,
    } == {
        first.correction.correction_id,
        second.correction.correction_id,
        third.correction.correction_id,
    }
    assert (
        len(
            {
                first.correction.correction_id,
                second.correction.correction_id,
                third.correction.correction_id,
            }
        )
        == 3
    )
    assert third.position_snapshot.accrued_swap_total == Decimal("120")

    with sqlite3.connect(path) as connection:
        amounts = [
            Decimal(row[0])
            for row in connection.execute(
                "SELECT amount FROM live_paper_ledger_entries "
                "WHERE entry_kind IN ('SWAP_ACCRUAL', 'SWAP_ACCRUAL_CORRECTION') "
                "ORDER BY ledger_entry_seq ASC"
            )
        ]
    assert amounts == [Decimal("100"), Decimal("20"), Decimal("-20"), Decimal("20")]


def test_correction_chain_integrity_rejects_wrong_ordinal_and_wrong_predecessor(
    tmp_path: Path,
) -> None:
    path = tmp_path / "live.sqlite"
    store = SQLitePaperStore(path)
    account = bootstrap()
    mark = observation()
    store.append_market_observations((mark,))
    position_id, snapshot_id = _open_entry_position(
        store, account, quantity=Decimal("1000"), mark=mark, key="a"
    )
    accrual_result = store.accrue_or_skip_swap(
        paper_position_snapshot_id=snapshot_id,
        evidence=_evidence(long_received_amount=Decimal("100")),
        rollover_date=date(2026, 1, 2),
        policy=_swap_policy(unit_basis_base_units=(("STANDARD_LOT", Decimal("1000")),)),
        mark_observations=(mark,),
        resolved_at=LATER,
    )
    accrual_id = accrual_result.accrual.paper_swap_accrual_id

    with pytest.raises(PaperLedgerIntegrityError):
        store.correct_swap_accrual(
            corrected_accrual_id=accrual_id,
            chain_ordinal=2,
            predecessor_correction_id=None,
            replacement_amount=Decimal("120"),
            correction_reason="bad-ordinal",
            swap_evidence_id="ev-x",
            mark_observations=(mark,),
            resolved_at=LATER,
        )

    store.correct_swap_accrual(
        corrected_accrual_id=accrual_id,
        chain_ordinal=1,
        predecessor_correction_id=None,
        replacement_amount=Decimal("120"),
        correction_reason="ok",
        swap_evidence_id="ev-1",
        mark_observations=(mark,),
        resolved_at=LATER,
    )
    with pytest.raises(PaperLedgerIntegrityError):
        store.correct_swap_accrual(
            corrected_accrual_id=accrual_id,
            chain_ordinal=2,
            predecessor_correction_id="wrong-predecessor",
            replacement_amount=Decimal("130"),
            correction_reason="bad-predecessor",
            swap_evidence_id="ev-2",
            mark_observations=(mark,),
            resolved_at=LATER,
        )
    with sqlite3.connect(path) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM live_paper_swap_accrual_corrections"
        ).fetchone()[0]
    assert count == 1


def _liquidate_full_position(store: SQLitePaperStore, account, position_id: str, mark):
    from swap_bot.models import ApprovedLiquidationIntent, PositionId, Side

    liq_intent = ApprovedLiquidationIntent(
        ExecutionIntentId("liq-a"),
        RiskDecisionId("risk-liq-a"),
        PositionId(position_id),
        PAIR,
        Decimal("1000"),
        "liq-idem-a",
        NOW,
    )
    accepted_liq = store.accept_emergency_liquidation_order(
        fill_policy=fill_policy(maximum_steps=1),
        account_bootstrap=account,
        intent=liq_intent,
        existing_position_side=Side.BUY,
        evaluated_at=NOW,
    )
    step = store.create_step(plan=accepted_liq.plan, ordinal=0, evaluated_at=NOW)
    return store.evaluate_step(
        step=step.step,
        plan=accepted_liq.plan,
        worker_identity="w1",
        evaluated_at=NOW,
        mark_observations=(mark,),
    )


def _matched_liquidation_flow(tmp_path: Path):
    path = tmp_path / "live.sqlite"
    store = SQLitePaperStore(path)
    account = bootstrap()
    mark = observation()
    store.append_market_observations((mark,))
    position_id, _snapshot_id = _open_entry_position(
        store, account, quantity=Decimal("1000"), mark=mark, key="a"
    )
    result = _liquidate_full_position(store, account, position_id, mark)
    assert result.position_fill_application is not None
    assert result.ledger_entry is not None
    matched = store.reconcile_account(paper_account_id=account.paper_account_id, resolved_at=LATER)
    assert matched.outcome is PaperReconciliationOutcome.MATCHED
    return path, store, account, result


def test_reconciliation_tampered_position_fill_application_reports_mismatch(tmp_path: Path) -> None:
    from swap_bot.paper import PaperLedgerEntry, PaperPositionFillApplication

    path, store, account, result = _matched_liquidation_flow(tmp_path)
    application = result.position_fill_application
    entry = result.ledger_entry

    forged_application = PaperPositionFillApplication.create(
        paper_position_id=application.paper_position_id,
        paper_order_id=application.paper_order_id,
        paper_fill_id=application.paper_fill_id,
        application_kind=application.application_kind,
        quantity=application.quantity,
        price=application.price,
        open_quantity_after=Decimal("999999"),
        realized_pnl_amount=application.realized_pnl_amount,
        created_at=application.created_at,
    )
    # A hypothetical bug that produced the wrong open_quantity_after at write time
    # would still consistently bind the ledger entry to that same (wrong) ID, so its
    # own identity (which commits to source_evidence_id) must move together with it.
    forged_entry = PaperLedgerEntry.create(
        paper_account_id=entry.paper_account_id,
        paper_position_id=entry.paper_position_id,
        entry_kind=entry.entry_kind,
        settlement_currency=entry.settlement_currency,
        amount=entry.amount,
        source_evidence_kind=entry.source_evidence_kind,
        source_evidence_id=forged_application.paper_position_fill_application_id,
        formula_version=entry.formula_version,
        created_at=entry.created_at,
    )
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TRIGGER live_paper_position_fill_applications_no_update")
        connection.execute("DROP TRIGGER live_paper_ledger_entries_no_update")
        connection.execute(
            "UPDATE live_paper_position_fill_applications "
            "SET paper_position_fill_application_id = ?, open_quantity_after = ? "
            "WHERE paper_position_fill_application_id = ?",
            (
                forged_application.paper_position_fill_application_id,
                "999999",
                application.paper_position_fill_application_id,
            ),
        )
        connection.execute(
            "UPDATE live_paper_ledger_entries SET ledger_entry_id = ?, source_evidence_id = ? "
            "WHERE ledger_entry_id = ?",
            (
                forged_entry.ledger_entry_id,
                forged_application.paper_position_fill_application_id,
                entry.ledger_entry_id,
            ),
        )

    tampered = store.reconcile_account(
        paper_account_id=account.paper_account_id, resolved_at=LATER + timedelta(seconds=1)
    )
    assert tampered.outcome is PaperReconciliationOutcome.MISMATCHED
    assert PaperReconciledRecordKind.POSITION_FILL_APPLICATION in tampered.mismatched_record_kinds
    assert forged_application.paper_position_fill_application_id in tampered.mismatched_record_ids
    # The ledger amount itself was never touched, so LEDGER_ENTRY does not also mismatch.
    assert PaperReconciledRecordKind.LEDGER_ENTRY not in tampered.mismatched_record_kinds


def test_reconciliation_tampered_ledger_entry_reports_mismatch(tmp_path: Path) -> None:
    from swap_bot.paper import PaperLedgerEntry

    path, store, account, result = _matched_liquidation_flow(tmp_path)
    entry = result.ledger_entry
    forged_entry = PaperLedgerEntry.create(
        paper_account_id=entry.paper_account_id,
        paper_position_id=entry.paper_position_id,
        entry_kind=entry.entry_kind,
        settlement_currency=entry.settlement_currency,
        amount=Decimal("999999"),
        source_evidence_kind=entry.source_evidence_kind,
        source_evidence_id=entry.source_evidence_id,
        formula_version=entry.formula_version,
        created_at=entry.created_at,
    )
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TRIGGER live_paper_ledger_entries_no_update")
        connection.execute(
            "UPDATE live_paper_ledger_entries SET ledger_entry_id = ?, amount = ? "
            "WHERE ledger_entry_id = ?",
            (forged_entry.ledger_entry_id, "999999", entry.ledger_entry_id),
        )
    tampered = store.reconcile_account(
        paper_account_id=account.paper_account_id, resolved_at=LATER + timedelta(seconds=1)
    )
    assert tampered.outcome is PaperReconciliationOutcome.MISMATCHED
    assert PaperReconciledRecordKind.LEDGER_ENTRY in tampered.mismatched_record_kinds
    assert forged_entry.ledger_entry_id in tampered.mismatched_record_ids


def test_reconciliation_tampered_position_snapshot_reports_mismatch(tmp_path: Path) -> None:
    from swap_bot.paper import PaperPositionSnapshot

    path, store, account, result = _matched_liquidation_flow(tmp_path)
    snapshot = result.position_snapshot
    forged_snapshot = PaperPositionSnapshot.create(
        paper_account_id=snapshot.paper_account_id,
        paper_position_id=snapshot.paper_position_id,
        pair=snapshot.pair,
        position_side=snapshot.position_side,
        open_quantity=Decimal("424242"),
        average_entry_price=snapshot.average_entry_price,
        realized_pnl_total=snapshot.realized_pnl_total,
        accrued_swap_total=snapshot.accrued_swap_total,
        highest_application_seq=snapshot.highest_application_seq,
        highest_ledger_entry_seq=snapshot.highest_ledger_entry_seq,
        created_at=snapshot.created_at,
    )
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TRIGGER live_paper_position_snapshots_no_update")
        connection.execute(
            "UPDATE live_paper_position_snapshots "
            "SET paper_position_snapshot_id = ?, open_quantity = ? "
            "WHERE paper_position_snapshot_id = ?",
            (
                forged_snapshot.paper_position_snapshot_id,
                "424242",
                snapshot.paper_position_snapshot_id,
            ),
        )
    tampered = store.reconcile_account(
        paper_account_id=account.paper_account_id, resolved_at=LATER + timedelta(seconds=1)
    )
    assert tampered.outcome is PaperReconciliationOutcome.MISMATCHED
    assert PaperReconciledRecordKind.POSITION_SNAPSHOT in tampered.mismatched_record_kinds
    assert forged_snapshot.paper_position_snapshot_id in tampered.mismatched_record_ids


def test_reconciliation_tampered_account_snapshot_reports_mismatch(tmp_path: Path) -> None:
    from swap_bot.paper import PaperAccountSnapshot

    path, store, account, result = _matched_liquidation_flow(tmp_path)
    snapshot = result.account_snapshot
    forged_snapshot = PaperAccountSnapshot.create(
        paper_account_id=snapshot.paper_account_id,
        cash=snapshot.cash,
        realized_pnl_total=snapshot.realized_pnl_total,
        unrealized_pnl_total=snapshot.unrealized_pnl_total,
        accrued_swap_total=snapshot.accrued_swap_total,
        equity=Decimal("424242"),
        used_margin=snapshot.used_margin,
        available_margin=snapshot.available_margin,
        gross_exposure=snapshot.gross_exposure,
        open_position_count=snapshot.open_position_count,
        open_order_count=snapshot.open_order_count,
        mark_observation_ids=snapshot.mark_observation_ids,
        highest_application_seq=snapshot.highest_application_seq,
        highest_ledger_entry_seq=snapshot.highest_ledger_entry_seq,
        highest_order_event_seq=snapshot.highest_order_event_seq,
        margin_policy_version=snapshot.margin_policy_version,
        unrealized_mark_policy_version=snapshot.unrealized_mark_policy_version,
        created_at=snapshot.created_at,
    )
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TRIGGER live_paper_account_snapshots_no_update")
        connection.execute(
            "UPDATE live_paper_account_snapshots SET paper_account_snapshot_id = ?, equity = ? "
            "WHERE paper_account_snapshot_id = ?",
            (
                forged_snapshot.paper_account_snapshot_id,
                "424242",
                snapshot.paper_account_snapshot_id,
            ),
        )
    tampered = store.reconcile_account(
        paper_account_id=account.paper_account_id, resolved_at=LATER + timedelta(seconds=1)
    )
    assert tampered.outcome is PaperReconciliationOutcome.MISMATCHED
    assert PaperReconciledRecordKind.ACCOUNT_SNAPSHOT in tampered.mismatched_record_kinds
    assert forged_snapshot.paper_account_snapshot_id in tampered.mismatched_record_ids
