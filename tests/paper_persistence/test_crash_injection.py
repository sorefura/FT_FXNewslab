"""Injected-failure boundary tests.

acceptance.md requires an injected failure at each write boundary (order, plan,
Step, terminal claim, selection, Fill, position application, ledger entry, position
snapshot, account snapshot, rollover claim, accrual, non-accrual, correction,
consumption, release, reconciliation result) to roll back that entire transaction
and leave zero rows from it. Every write in paper/store.py goes through one of the
two module-level append-or-compare helpers, so patching those two helpers to raise
at a named table injects a failure at that exact boundary without a bespoke hook
per table.
"""

import sqlite3
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from swap_bot.models import PositionId, Side
from swap_bot.paper import PaperSwapAccrualPolicy, SQLitePaperStore
from swap_bot.paper import store as store_module
from swap_bot.strategy.swap_evidence import OperationalSwapEvidence
from swap_bot.swap import SwapAvailability

from ._helpers import (
    JPY,
    NOW,
    PAIR,
    bootstrap,
    execution_intent,
    fill_policy,
    insert_m2d_intent,
    liquidation_intent,
    observation,
    synthetic_close_intent,
)


def _all_counts(path: Path) -> dict[str, int]:
    with sqlite3.connect(path) as connection:
        names = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'live_paper_%'"
            ).fetchall()
        ]
        return {
            name: connection.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
            for name in names
        }


def _fail_at(monkeypatch: pytest.MonkeyPatch, target_table: str) -> None:
    real_insert = store_module._insert_or_compare
    real_insert_seq = store_module._insert_or_compare_returning_seq

    def patched_insert(connection, table, *args, **kwargs):  # type: ignore[no-untyped-def]
        if table == target_table:
            raise RuntimeError(f"injected failure at {target_table}")
        return real_insert(connection, table, *args, **kwargs)

    def patched_insert_seq(connection, table, *args, **kwargs):  # type: ignore[no-untyped-def]
        if table == target_table:
            raise RuntimeError(f"injected failure at {target_table}")
        return real_insert_seq(connection, table, *args, **kwargs)

    monkeypatch.setattr(store_module, "_insert_or_compare", patched_insert)
    monkeypatch.setattr(store_module, "_insert_or_compare_returning_seq", patched_insert_seq)


def _swap_policy() -> PaperSwapAccrualPolicy:
    return PaperSwapAccrualPolicy.create(
        policy_version="swap-policy-v1",
        unit_basis_base_units=(("STANDARD_LOT", Decimal("100000")),),
        maximum_swap_age=timedelta(days=3),
        settlement_currency=JPY,
    )


def _swap_evidence() -> OperationalSwapEvidence:
    return OperationalSwapEvidence.create(
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


def _accrued_position_snapshot(tmp_path: Path):  # type: ignore[no-untyped-def]
    path = tmp_path / "live.sqlite"
    store = SQLitePaperStore(path)
    mark = observation()
    store.append_market_observations((mark,))
    accepted = store.accept_entry_order(
        fill_policy=fill_policy(maximum_steps=1),
        account_bootstrap=bootstrap(),
        intent=execution_intent(),
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
    return path, store, result.position_snapshot, mark


# ---------------------------------------------------------------------------
# T1: order, plan
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "target_table", ["live_paper_orders", "live_paper_fill_evaluation_plans"]
)
def test_injected_failure_during_t1_rolls_back_the_whole_order_acceptance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target_table: str
) -> None:
    path = tmp_path / "live.sqlite"
    store = SQLitePaperStore(path)
    before = _all_counts(path)
    _fail_at(monkeypatch, target_table)
    with pytest.raises(RuntimeError, match="injected failure"):
        store.accept_entry_order(
            fill_policy=fill_policy(),
            account_bootstrap=bootstrap(),
            intent=execution_intent(),
            evaluated_at=NOW,
        )
    assert _all_counts(path) == before


# ---------------------------------------------------------------------------
# T2: Step
# ---------------------------------------------------------------------------


def test_injected_failure_during_t2_rolls_back_the_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "live.sqlite"
    store = SQLitePaperStore(path)
    accepted = store.accept_entry_order(
        fill_policy=fill_policy(),
        account_bootstrap=bootstrap(),
        intent=execution_intent(),
        evaluated_at=NOW,
    )
    before = _all_counts(path)
    _fail_at(monkeypatch, "live_paper_fill_evaluation_steps")
    with pytest.raises(RuntimeError, match="injected failure"):
        store.create_step(plan=accepted.plan, ordinal=0, evaluated_at=NOW)
    assert _all_counts(path) == before


# ---------------------------------------------------------------------------
# T3a MARKET_SELECTED: terminal claim, selection, Fill, position application,
# position snapshot, account snapshot
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "target_table",
    [
        "live_paper_step_terminal_claims",
        "live_paper_market_observation_selections",
        "live_paper_fills",
        "live_paper_position_fill_applications",
        "live_paper_position_snapshots",
        "live_paper_account_snapshots",
    ],
)
def test_injected_failure_during_t3a_rolls_back_the_whole_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target_table: str
) -> None:
    path = tmp_path / "live.sqlite"
    store = SQLitePaperStore(path)
    mark = observation()
    store.append_market_observations((mark,))
    accepted = store.accept_entry_order(
        fill_policy=fill_policy(maximum_steps=1),
        account_bootstrap=bootstrap(),
        intent=execution_intent(),
        evaluated_at=NOW,
    )
    step = store.create_step(plan=accepted.plan, ordinal=0, evaluated_at=NOW)
    before = _all_counts(path)
    _fail_at(monkeypatch, target_table)
    with pytest.raises(RuntimeError, match="injected failure"):
        store.evaluate_step(
            step=step.step,
            plan=accepted.plan,
            worker_identity="w1",
            evaluated_at=NOW,
            mark_observations=(mark,),
        )
    assert _all_counts(path) == before


# ---------------------------------------------------------------------------
# T3a REDUCE_ONLY: ledger entry
# ---------------------------------------------------------------------------


def test_injected_failure_at_ledger_entry_boundary_rolls_back_the_reduce_only_fill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "live.sqlite"
    store = SQLitePaperStore(path)
    mark = observation()
    store.append_market_observations((mark,))
    entry_accepted = store.accept_entry_order(
        fill_policy=fill_policy(maximum_steps=1),
        account_bootstrap=bootstrap(),
        intent=execution_intent(),
        evaluated_at=NOW,
    )
    entry_step = store.create_step(plan=entry_accepted.plan, ordinal=0, evaluated_at=NOW)
    store.evaluate_step(
        step=entry_step.step,
        plan=entry_accepted.plan,
        worker_identity="w1",
        evaluated_at=NOW,
        mark_observations=(mark,),
    )
    position_id = entry_accepted.order.intent_lineage.paper_position_id

    liq_intent = liquidation_intent(
        position_id=PositionId(position_id), quantity=Decimal("1000")
    )
    liq_accepted = store.accept_emergency_liquidation_order(
        fill_policy=fill_policy(maximum_steps=1),
        account_bootstrap=bootstrap(),
        intent=liq_intent,
        existing_position_side=Side.BUY,
        evaluated_at=NOW,
    )
    liq_step = store.create_step(plan=liq_accepted.plan, ordinal=0, evaluated_at=NOW)

    before = _all_counts(path)
    _fail_at(monkeypatch, "live_paper_ledger_entries")
    with pytest.raises(RuntimeError, match="injected failure"):
        store.evaluate_step(
            step=liq_step.step,
            plan=liq_accepted.plan,
            worker_identity="w1",
            evaluated_at=NOW,
            mark_observations=(mark,),
        )
    assert _all_counts(path) == before


# ---------------------------------------------------------------------------
# T3a ordinary close: consumption
# ---------------------------------------------------------------------------


def test_injected_failure_at_consumption_boundary_rolls_back_the_close_fill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "live.sqlite"
    store = SQLitePaperStore(path)
    mark = observation()
    store.append_market_observations((mark,))
    entry_accepted = store.accept_entry_order(
        fill_policy=fill_policy(maximum_steps=1),
        account_bootstrap=bootstrap(),
        intent=execution_intent(),
        evaluated_at=NOW,
    )
    entry_step = store.create_step(plan=entry_accepted.plan, ordinal=0, evaluated_at=NOW)
    store.evaluate_step(
        step=entry_step.step,
        plan=entry_accepted.plan,
        worker_identity="w1",
        evaluated_at=NOW,
        mark_observations=(mark,),
    )
    position_id = entry_accepted.order.intent_lineage.paper_position_id

    close_intent = synthetic_close_intent(
        position_id=PositionId(position_id), quantity=Decimal("1000")
    )
    insert_m2d_intent(path, close_intent)
    close_accepted = store.accept_ordinary_close_order(
        fill_policy=fill_policy(maximum_steps=2),
        account_bootstrap=bootstrap(),
        intent=close_intent,
        evaluated_at=NOW,
    )
    close_step = store.create_step(plan=close_accepted.plan, ordinal=0, evaluated_at=NOW)

    before = _all_counts(path)
    _fail_at(monkeypatch, "live_paper_reservation_consumptions")
    with pytest.raises(RuntimeError, match="injected failure"):
        store.evaluate_step(
            step=close_step.step,
            plan=close_accepted.plan,
            worker_identity="w1",
            evaluated_at=NOW,
            mark_observations=(mark,),
        )
    assert _all_counts(path) == before


# ---------------------------------------------------------------------------
# T3c no-market terminal ordinary close: release
# ---------------------------------------------------------------------------


def test_injected_failure_at_release_boundary_rolls_back_the_no_market_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "live.sqlite"
    store = SQLitePaperStore(path)
    mark = observation()
    store.append_market_observations((mark,))
    entry_accepted = store.accept_entry_order(
        fill_policy=fill_policy(maximum_steps=1),
        account_bootstrap=bootstrap(),
        intent=execution_intent(),
        evaluated_at=NOW,
    )
    entry_step = store.create_step(plan=entry_accepted.plan, ordinal=0, evaluated_at=NOW)
    store.evaluate_step(
        step=entry_step.step,
        plan=entry_accepted.plan,
        worker_identity="w1",
        evaluated_at=NOW,
        mark_observations=(mark,),
    )
    position_id = entry_accepted.order.intent_lineage.paper_position_id

    # A close intent created an hour after the only persisted observation makes that
    # observation ineligible for the close plan's own window (clause 3), so its Step 0
    # has no eligible candidate at all and resolves NO_MARKET as soon as it is due.
    later_created_at = NOW + timedelta(hours=1)
    close_intent = synthetic_close_intent(
        position_id=PositionId(position_id),
        quantity=Decimal("1000"),
        created_at=later_created_at,
    )
    insert_m2d_intent(path, close_intent)
    close_accepted = store.accept_ordinary_close_order(
        fill_policy=fill_policy(maximum_steps=1),
        account_bootstrap=bootstrap(),
        intent=close_intent,
        evaluated_at=later_created_at,
    )
    close_step = store.create_step(
        plan=close_accepted.plan, ordinal=0, evaluated_at=later_created_at
    )
    after_due = close_step.step.evaluation_due_at + timedelta(seconds=1)

    before = _all_counts(path)
    _fail_at(monkeypatch, "live_paper_reservation_releases")
    with pytest.raises(RuntimeError, match="injected failure"):
        store.evaluate_step(
            step=close_step.step,
            plan=close_accepted.plan,
            worker_identity="w1",
            evaluated_at=after_due,
        )
    assert _all_counts(path) == before


# ---------------------------------------------------------------------------
# T5: rollover claim, accrual, non-accrual
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "target_table", ["live_paper_swap_rollover_claims", "live_paper_swap_accruals"]
)
def test_injected_failure_during_t5_accrued_rolls_back_the_whole_rollover(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target_table: str
) -> None:
    path, store, position_snapshot, mark = _accrued_position_snapshot(tmp_path)
    before = _all_counts(path)
    _fail_at(monkeypatch, target_table)
    with pytest.raises(RuntimeError, match="injected failure"):
        store.accrue_or_skip_swap(
            paper_position_snapshot_id=position_snapshot.paper_position_snapshot_id,
            evidence=_swap_evidence(),
            rollover_date=date(2026, 1, 2),
            policy=_swap_policy(),
            mark_observations=(mark,),
            resolved_at=NOW + timedelta(minutes=5),
        )
    assert _all_counts(path) == before


def test_injected_failure_during_t5_non_accrued_rolls_back_the_claim_and_non_accrual(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, store, position_snapshot, _mark = _accrued_position_snapshot(tmp_path)
    before = _all_counts(path)
    _fail_at(monkeypatch, "live_paper_swap_non_accruals")
    with pytest.raises(RuntimeError, match="injected failure"):
        store.accrue_or_skip_swap(
            paper_position_snapshot_id=position_snapshot.paper_position_snapshot_id,
            evidence=None,
            rollover_date=date(2026, 1, 2),
            policy=_swap_policy(),
            mark_observations=(),
            resolved_at=NOW + timedelta(minutes=5),
        )
    assert _all_counts(path) == before


# ---------------------------------------------------------------------------
# T6: correction
# ---------------------------------------------------------------------------


def test_injected_failure_during_t6_rolls_back_the_correction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, store, position_snapshot, mark = _accrued_position_snapshot(tmp_path)
    accrual_result = store.accrue_or_skip_swap(
        paper_position_snapshot_id=position_snapshot.paper_position_snapshot_id,
        evidence=_swap_evidence(),
        rollover_date=date(2026, 1, 2),
        policy=_swap_policy(),
        mark_observations=(mark,),
        resolved_at=NOW + timedelta(minutes=5),
    )
    assert accrual_result.accrual is not None
    before = _all_counts(path)
    _fail_at(monkeypatch, "live_paper_swap_accrual_corrections")
    with pytest.raises(RuntimeError, match="injected failure"):
        store.correct_swap_accrual(
            corrected_accrual_id=accrual_result.accrual.paper_swap_accrual_id,
            chain_ordinal=1,
            predecessor_correction_id=None,
            replacement_amount=Decimal("50"),
            correction_reason="late evidence",
            swap_evidence_id="swap-evidence-correction-1",
            mark_observations=(mark,),
            resolved_at=NOW + timedelta(minutes=10),
        )
    assert _all_counts(path) == before


# ---------------------------------------------------------------------------
# T7: reconciliation result
# ---------------------------------------------------------------------------


def test_injected_failure_during_t7_rolls_back_the_reconciliation_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "live.sqlite"
    store = SQLitePaperStore(path)
    account = bootstrap()
    store.accept_entry_order(
        fill_policy=fill_policy(),
        account_bootstrap=account,
        intent=execution_intent(),
        evaluated_at=NOW,
    )
    before = _all_counts(path)
    _fail_at(monkeypatch, "live_paper_reconciliation_results")
    with pytest.raises(RuntimeError, match="injected failure"):
        store.reconcile_account(paper_account_id=account.paper_account_id, resolved_at=NOW)
    assert _all_counts(path) == before
