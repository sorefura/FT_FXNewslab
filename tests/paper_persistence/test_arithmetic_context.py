import decimal
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from swap_bot.models import PositionId, Side
from swap_bot.paper import (
    PAPER_EXACT_ARITHMETIC_V1,
    PAPER_QUOTIENT_ARITHMETIC_V1,
    PaperPartialFillMode,
    PaperReconciliationOutcome,
    SQLitePaperStore,
    StepResolutionOutcome,
)

from tests.paper_persistence._helpers import (
    NOW,
    bootstrap,
    execution_intent,
    fill_policy,
    liquidation_intent,
    observation,
)


def test_persisted_totals_keep_full_precision_for_non_terminating_basis(tmp_path: Path) -> None:
    # Entry fills of 200 at 101 and 100 at 100 (basis 30200/300 does not
    # terminate), closed in full at 105. The realized total needs 35 significant
    # digits, so a sum taken under the interpreter's default 28-digit context
    # rounds it to 1300 while the rebuild keeps it exact.
    path = tmp_path / "live.sqlite"
    store = SQLitePaperStore(path)
    account = bootstrap()
    entry_policy = fill_policy(
        maximum_steps=2,
        partial_fill_mode=PaperPartialFillMode.FRACTION_OF_REMAINING,
        partial_fill_fraction=Decimal("0.5"),
    )
    first_mark = observation(bid=Decimal("100.5"), ask=Decimal("101"))
    store.append_market_observations((first_mark,))

    accepted = store.accept_entry_order(
        fill_policy=entry_policy,
        account_bootstrap=account,
        intent=execution_intent(quantity=Decimal("400")),
        evaluated_at=NOW,
    )
    step0 = store.create_step(plan=accepted.plan, ordinal=0, evaluated_at=NOW)
    result0 = store.evaluate_step(
        step=step0.step,
        plan=accepted.plan,
        worker_identity="w1",
        evaluated_at=NOW,
        mark_observations=(first_mark,),
    )
    assert result0.fill is not None
    assert result0.fill.fill_quantity == Decimal("200.0")
    assert result0.fill.fill_price == Decimal("101")

    step1_at = step0.step.evaluation_due_at + entry_policy.step_gap
    second_mark = observation(
        bid=Decimal("99.9"),
        ask=Decimal("100"),
        received_at=step1_at,
        provider_observed_at=step1_at,
    )
    store.append_market_observations((second_mark,))
    step1 = store.create_step(plan=accepted.plan, ordinal=1, evaluated_at=step1_at)
    result1 = store.evaluate_step(
        step=step1.step,
        plan=accepted.plan,
        worker_identity="w1",
        evaluated_at=step1_at,
        mark_observations=(second_mark,),
    )
    assert result1.fill is not None
    assert result1.fill.fill_quantity == Decimal("100.0")
    assert result1.fill.fill_price == Decimal("100")

    position_id = accepted.order.intent_lineage.paper_position_id
    close_at = step1_at + timedelta(minutes=1)
    close_mark = observation(
        bid=Decimal("105"),
        ask=Decimal("105.1"),
        received_at=close_at,
        provider_observed_at=close_at,
    )
    store.append_market_observations((close_mark,))
    accepted_liquidation = store.accept_emergency_liquidation_order(
        fill_policy=fill_policy(maximum_steps=1),
        account_bootstrap=account,
        intent=liquidation_intent(
            position_id=PositionId(position_id),
            quantity=Decimal("300"),
            created_at=close_at,
        ),
        existing_position_side=Side.BUY,
        evaluated_at=close_at,
    )
    close_step = store.create_step(
        plan=accepted_liquidation.plan, ordinal=0, evaluated_at=close_at
    )
    closed = store.evaluate_step(
        step=close_step.step,
        plan=accepted_liquidation.plan,
        worker_identity="w1",
        evaluated_at=close_at,
        mark_observations=(close_mark,),
    )
    assert closed.outcome is StepResolutionOutcome.T3A
    assert closed.fill is not None and closed.fill.fill_price == Decimal("105")

    with decimal.localcontext(PAPER_QUOTIENT_ARITHMETIC_V1):
        basis = (Decimal("200.0") * Decimal("101") + Decimal("100.0") * Decimal("100")) / Decimal(
            "300.0"
        )
    with decimal.localcontext(PAPER_EXACT_ARITHMETIC_V1):
        expected_realized = (Decimal("105") - basis) * Decimal("300")
    assert str(expected_realized) == "1299.9999999999999999999999999999900"

    assert closed.position_snapshot is not None
    assert closed.account_snapshot is not None
    assert closed.position_snapshot.open_quantity == 0
    assert closed.position_snapshot.realized_pnl_total == expected_realized
    assert str(closed.position_snapshot.realized_pnl_total) == str(expected_realized)
    assert closed.account_snapshot.realized_pnl_total == expected_realized
    assert str(closed.account_snapshot.realized_pnl_total) == str(expected_realized)

    reconciled = store.reconcile_account(
        paper_account_id=account.paper_account_id, resolved_at=close_at + timedelta(minutes=1)
    )
    assert reconciled.outcome is PaperReconciliationOutcome.MATCHED
    assert reconciled.mismatched_record_kinds == ()
    assert reconciled.mismatched_record_ids == ()
