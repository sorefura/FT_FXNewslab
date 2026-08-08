import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest
from swap_bot.execution_authority import ExecutionAuthorityMode
from swap_bot.models import CandidateId, ExecutionIntentId, PositionId, RiskDecisionId, Side
from swap_bot.paper import (
    PaperOrderState,
    PaperPartialFillMode,
    PaperPersistenceConflict,
    PaperPersistenceIntegrityError,
    SQLitePaperStore,
    StepResolutionOutcome,
)

from tests.paper_persistence._helpers import (
    NOW,
    OTHER_PAIR,
    PAIR,
    bootstrap,
    execution_intent,
    fill_policy,
    insert_m2d_intent,
    observation,
    synthetic_close_intent,
)


def _open_entry_position(store: SQLitePaperStore, account, *, quantity: Decimal, mark) -> str:
    intent = execution_intent(
        intent_id=ExecutionIntentId(f"intent-entry-{quantity}"),
        candidate_id=CandidateId(f"candidate-entry-{quantity}"),
        risk_decision_id=RiskDecisionId(f"risk-entry-{quantity}"),
        quantity=quantity,
        idempotency_key=f"entry-idem-{quantity}",
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
    return accepted.order.intent_lineage.paper_position_id


def test_t3b_numeric_example_consumes_400_and_releases_600(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    store = SQLitePaperStore(path)
    account = bootstrap()
    mark = observation()
    store.append_market_observations((mark,))
    position_id = _open_entry_position(store, account, quantity=Decimal("1000"), mark=mark)

    close_intent = synthetic_close_intent(
        position_id=PositionId(position_id), quantity=Decimal("1000")
    )
    insert_m2d_intent(path, close_intent)

    close_policy = fill_policy(
        maximum_steps=1,
        partial_fill_mode=PaperPartialFillMode.FRACTION_OF_REMAINING,
        partial_fill_fraction=Decimal("0.4"),
    )
    accepted_close = store.accept_ordinary_close_order(
        fill_policy=close_policy,
        account_bootstrap=account,
        intent=close_intent,
        evaluated_at=NOW,
    )
    step = store.create_step(plan=accepted_close.plan, ordinal=0, evaluated_at=NOW)
    result = store.evaluate_step(
        step=step.step,
        plan=accepted_close.plan,
        worker_identity="w1",
        evaluated_at=NOW,
        mark_observations=(mark,),
    )

    assert result.outcome is StepResolutionOutcome.T3B
    assert result.order_events[0].state is PaperOrderState.PARTIALLY_FILLED
    assert result.order_events[1].state is PaperOrderState.CANCELLED
    assert result.reservation_consumption is not None
    assert result.reservation_consumption.consumed_quantity == Decimal("400.0")
    assert result.reservation_release is not None
    assert result.reservation_release.released_quantity == Decimal("600.0")

    consumed = result.reservation_consumption.consumed_quantity
    released = result.reservation_release.released_quantity
    outstanding = close_intent.quantity - consumed - released
    assert consumed + outstanding + released == close_intent.quantity
    assert outstanding == 0


def test_conservation_equation_holds_after_two_partial_fills_and_terminal_release(
    tmp_path: Path,
) -> None:
    path = tmp_path / "live.sqlite"
    store = SQLitePaperStore(path)
    account = bootstrap()
    mark = observation()
    store.append_market_observations((mark,))
    position_id = _open_entry_position(store, account, quantity=Decimal("1000"), mark=mark)

    close_intent = synthetic_close_intent(
        position_id=PositionId(position_id), quantity=Decimal("1000")
    )
    insert_m2d_intent(path, close_intent)
    close_policy = fill_policy(
        maximum_steps=2,
        partial_fill_mode=PaperPartialFillMode.FRACTION_OF_REMAINING,
        partial_fill_fraction=Decimal("0.3"),
    )
    accepted_close = store.accept_ordinary_close_order(
        fill_policy=close_policy,
        account_bootstrap=account,
        intent=close_intent,
        evaluated_at=NOW,
    )

    def _conservation() -> tuple[Decimal, Decimal, Decimal]:
        with sqlite3.connect(path) as connection:
            consumed = Decimal(
                sum(
                    Decimal(row[0])
                    for row in connection.execute(
                        "SELECT consumed_quantity FROM live_paper_reservation_consumptions "
                        "WHERE close_intent_idempotency_key = ?",
                        (close_intent.idempotency_key,),
                    )
                )
            )
            released_row = connection.execute(
                "SELECT released_quantity FROM live_paper_reservation_releases "
                "WHERE close_intent_idempotency_key = ?",
                (close_intent.idempotency_key,),
            ).fetchone()
            released = Decimal(released_row[0]) if released_row is not None else Decimal(0)
        outstanding = close_intent.quantity - consumed - released
        return consumed, outstanding, released

    consumed, outstanding, released = _conservation()
    assert (consumed, outstanding, released) == (Decimal(0), Decimal("1000"), Decimal(0))

    step0 = store.create_step(plan=accepted_close.plan, ordinal=0, evaluated_at=NOW)
    result0 = store.evaluate_step(
        step=step0.step,
        plan=accepted_close.plan,
        worker_identity="w1",
        evaluated_at=NOW,
        mark_observations=(mark,),
    )
    assert result0.outcome is StepResolutionOutcome.T3A
    consumed, outstanding, released = _conservation()
    assert consumed + outstanding + released == close_intent.quantity
    assert consumed == Decimal("300.0")
    assert released == 0

    step1_window_start = step0.step.evaluation_due_at + close_policy.step_gap
    second_mark = observation(
        received_at=step1_window_start,
        provider_observed_at=step1_window_start,
    )
    store.append_market_observations((second_mark,))
    step1 = store.create_step(plan=accepted_close.plan, ordinal=1, evaluated_at=step1_window_start)
    result1 = store.evaluate_step(
        step=step1.step,
        plan=accepted_close.plan,
        worker_identity="w1",
        evaluated_at=step1_window_start,
        mark_observations=(second_mark,),
    )
    assert result1.outcome is StepResolutionOutcome.T3B
    consumed, outstanding, released = _conservation()
    assert consumed + outstanding + released == close_intent.quantity
    assert outstanding == 0
    assert consumed == Decimal("300.0") + Decimal("210.0")
    assert released == close_intent.quantity - consumed


def test_terminal_no_market_releases_the_full_remainder_with_no_fill(tmp_path: Path) -> None:
    from datetime import timedelta

    path = tmp_path / "live.sqlite"
    store = SQLitePaperStore(path)
    account = bootstrap()
    mark = observation()
    store.append_market_observations((mark,))
    position_id = _open_entry_position(store, account, quantity=Decimal("500"), mark=mark)

    close_intent = synthetic_close_intent(
        position_id=PositionId(position_id), quantity=Decimal("500")
    )
    insert_m2d_intent(path, close_intent)

    # A tight maximum_market_age makes the already-persisted mark stale relative to
    # this plan's own due boundary, so its Step 0 has no eligible observation at all.
    close_policy = fill_policy(maximum_steps=1, maximum_market_age=timedelta(seconds=1))
    accepted_close = store.accept_ordinary_close_order(
        fill_policy=close_policy,
        account_bootstrap=account,
        intent=close_intent,
        evaluated_at=NOW,
    )
    step = store.create_step(plan=accepted_close.plan, ordinal=0, evaluated_at=NOW)
    result = store.evaluate_step(
        step=step.step,
        plan=accepted_close.plan,
        worker_identity="w1",
        evaluated_at=step.step.evaluation_due_at + timedelta(seconds=1),
    )
    assert result.outcome is StepResolutionOutcome.T3C
    assert result.reservation_consumption is None
    assert result.reservation_release is not None
    assert result.reservation_release.released_quantity == Decimal("500")
    assert result.order_events[0].state is PaperOrderState.REJECTED


# ---------------------------------------------------------------------------
# Frozen reservation settlement model: the five fail-closed cases
# ---------------------------------------------------------------------------


def test_second_consumption_for_one_fill_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    store = SQLitePaperStore(path)
    account = bootstrap()
    mark = observation()
    store.append_market_observations((mark,))
    position_id = _open_entry_position(store, account, quantity=Decimal("500"), mark=mark)

    close_intent = synthetic_close_intent(
        position_id=PositionId(position_id), quantity=Decimal("500")
    )
    insert_m2d_intent(path, close_intent)
    accepted_close = store.accept_ordinary_close_order(
        fill_policy=fill_policy(maximum_steps=1),
        account_bootstrap=account,
        intent=close_intent,
        evaluated_at=NOW,
    )
    step = store.create_step(plan=accepted_close.plan, ordinal=0, evaluated_at=NOW)
    result = store.evaluate_step(
        step=step.step,
        plan=accepted_close.plan,
        worker_identity="w1",
        evaluated_at=NOW,
        mark_observations=(mark,),
    )
    assert result.reservation_consumption is not None

    with sqlite3.connect(path) as connection:
        before = connection.execute(
            "SELECT COUNT(*) FROM live_paper_reservation_consumptions"
        ).fetchone()[0]
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO live_paper_reservation_consumptions "
                "(consumption_id, contract_version, close_intent_idempotency_key, "
                "paper_order_id, paper_fill_id, consumed_quantity, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    "forged-consumption-1",
                    result.reservation_consumption.contract_version,
                    result.reservation_consumption.close_intent_idempotency_key,
                    result.reservation_consumption.paper_order_id,
                    result.reservation_consumption.paper_fill_id,
                    "1",
                    "2026-01-01T00:00:00+00:00",
                ),
            )
        after = connection.execute(
            "SELECT COUNT(*) FROM live_paper_reservation_consumptions"
        ).fetchone()[0]
    assert before == after


def test_second_release_for_one_order_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    store = SQLitePaperStore(path)
    account = bootstrap()
    mark = observation()
    store.append_market_observations((mark,))
    position_id = _open_entry_position(store, account, quantity=Decimal("500"), mark=mark)

    close_intent = synthetic_close_intent(
        position_id=PositionId(position_id), quantity=Decimal("500")
    )
    insert_m2d_intent(path, close_intent)
    # A partial-fill policy so the single Step leaves a positive remainder, forcing
    # T3B (a release) rather than T3A-FILLED (no release at all).
    close_policy = fill_policy(
        maximum_steps=1,
        partial_fill_mode=PaperPartialFillMode.FRACTION_OF_REMAINING,
        partial_fill_fraction=Decimal("0.4"),
    )
    accepted_close = store.accept_ordinary_close_order(
        fill_policy=close_policy,
        account_bootstrap=account,
        intent=close_intent,
        evaluated_at=NOW,
    )
    step = store.create_step(plan=accepted_close.plan, ordinal=0, evaluated_at=NOW)
    result = store.evaluate_step(
        step=step.step,
        plan=accepted_close.plan,
        worker_identity="w1",
        evaluated_at=NOW,
        mark_observations=(mark,),
    )
    assert result.outcome is StepResolutionOutcome.T3B
    assert result.reservation_release is not None

    with sqlite3.connect(path) as connection:
        before = connection.execute(
            "SELECT COUNT(*) FROM live_paper_reservation_releases"
        ).fetchone()[0]
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO live_paper_reservation_releases "
                "(release_id, contract_version, close_intent_idempotency_key, "
                "paper_order_id, terminal_order_state, released_quantity, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    "forged-release-1",
                    result.reservation_release.contract_version,
                    result.reservation_release.close_intent_idempotency_key,
                    result.reservation_release.paper_order_id,
                    "CANCELLED",
                    "1",
                    "2026-01-01T00:00:00+00:00",
                ),
            )
        after = connection.execute(
            "SELECT COUNT(*) FROM live_paper_reservation_releases"
        ).fetchone()[0]
    assert before == after


def test_consumption_after_a_release_exists_for_the_order_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    store = SQLitePaperStore(path)
    account = bootstrap()
    mark = observation()
    store.append_market_observations((mark,))
    position_id = _open_entry_position(store, account, quantity=Decimal("1000"), mark=mark)

    close_intent = synthetic_close_intent(
        position_id=PositionId(position_id), quantity=Decimal("1000")
    )
    insert_m2d_intent(path, close_intent)
    close_policy = fill_policy(
        maximum_steps=2,
        partial_fill_mode=PaperPartialFillMode.FRACTION_OF_REMAINING,
        partial_fill_fraction=Decimal("0.3"),
    )
    accepted_close = store.accept_ordinary_close_order(
        fill_policy=close_policy,
        account_bootstrap=account,
        intent=close_intent,
        evaluated_at=NOW,
    )
    step0 = store.create_step(plan=accepted_close.plan, ordinal=0, evaluated_at=NOW)
    result0 = store.evaluate_step(
        step=step0.step,
        plan=accepted_close.plan,
        worker_identity="w1",
        evaluated_at=NOW,
        mark_observations=(mark,),
    )
    assert result0.outcome is StepResolutionOutcome.T3A
    assert result0.reservation_release is None

    # A release for this order, structurally unreachable through the public API here
    # (a legitimate release only exists once the order is terminal, at which point no
    # further Step can be created for it), simulated directly to prove the guard.
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO live_paper_reservation_releases "
            "(release_id, contract_version, close_intent_idempotency_key, "
            "paper_order_id, terminal_order_state, released_quantity, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "phantom-release-1",
                "paper-reservation-release-v1",
                close_intent.idempotency_key,
                accepted_close.order.paper_order_id,
                "CANCELLED",
                "700",
                NOW.isoformat(),
            ),
        )
        applications_before = connection.execute(
            "SELECT COUNT(*) FROM live_paper_position_fill_applications"
        ).fetchone()[0]
        consumptions_before = connection.execute(
            "SELECT COUNT(*) FROM live_paper_reservation_consumptions"
        ).fetchone()[0]

    step1_window_start = step0.step.evaluation_due_at + close_policy.step_gap
    second_mark = observation(
        received_at=step1_window_start, provider_observed_at=step1_window_start
    )
    store.append_market_observations((second_mark,))
    step1 = store.create_step(plan=accepted_close.plan, ordinal=1, evaluated_at=step1_window_start)
    with pytest.raises(PaperPersistenceIntegrityError):
        store.evaluate_step(
            step=step1.step,
            plan=accepted_close.plan,
            worker_identity="w1",
            evaluated_at=step1_window_start,
            mark_observations=(second_mark,),
        )

    with sqlite3.connect(path) as connection:
        applications_after = connection.execute(
            "SELECT COUNT(*) FROM live_paper_position_fill_applications"
        ).fetchone()[0]
        consumptions_after = connection.execute(
            "SELECT COUNT(*) FROM live_paper_reservation_consumptions"
        ).fetchone()[0]
    assert applications_after == applications_before
    assert consumptions_after == consumptions_before


def test_consumption_exceeding_intent_quantity_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    store = SQLitePaperStore(path)
    account = bootstrap()
    mark = observation()
    store.append_market_observations((mark,))
    position_id = _open_entry_position(store, account, quantity=Decimal("1000"), mark=mark)

    with sqlite3.connect(path) as connection:
        entry_fill_id = connection.execute(
            "SELECT paper_fill_id FROM live_paper_fills LIMIT 1"
        ).fetchone()[0]

    close_intent = synthetic_close_intent(
        position_id=PositionId(position_id), quantity=Decimal("1000")
    )
    insert_m2d_intent(path, close_intent)
    close_policy = fill_policy(
        maximum_steps=2,
        partial_fill_mode=PaperPartialFillMode.FRACTION_OF_REMAINING,
        partial_fill_fraction=Decimal("0.3"),
    )
    accepted_close = store.accept_ordinary_close_order(
        fill_policy=close_policy,
        account_bootstrap=account,
        intent=close_intent,
        evaluated_at=NOW,
    )
    step0 = store.create_step(plan=accepted_close.plan, ordinal=0, evaluated_at=NOW)
    result0 = store.evaluate_step(
        step=step0.step,
        plan=accepted_close.plan,
        worker_identity="w1",
        evaluated_at=NOW,
        mark_observations=(mark,),
    )
    assert result0.reservation_consumption is not None
    assert result0.reservation_consumption.consumed_quantity == Decimal("300.0")

    # A consumption borrowing the entry order's own (otherwise-unused) Fill row,
    # bringing consumed_total to 1100 against an intent.quantity of 1000, so the next
    # legitimate consumption must be rejected rather than silently overshoot.
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO live_paper_reservation_consumptions "
            "(consumption_id, contract_version, close_intent_idempotency_key, "
            "paper_order_id, paper_fill_id, consumed_quantity, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "phantom-consumption-1",
                "paper-reservation-consumption-v1",
                close_intent.idempotency_key,
                accepted_close.order.paper_order_id,
                entry_fill_id,
                "800",
                NOW.isoformat(),
            ),
        )
        applications_before = connection.execute(
            "SELECT COUNT(*) FROM live_paper_position_fill_applications"
        ).fetchone()[0]
        releases_before = connection.execute(
            "SELECT COUNT(*) FROM live_paper_reservation_releases"
        ).fetchone()[0]

    step1_window_start = step0.step.evaluation_due_at + close_policy.step_gap
    second_mark = observation(
        received_at=step1_window_start, provider_observed_at=step1_window_start
    )
    store.append_market_observations((second_mark,))
    step1 = store.create_step(plan=accepted_close.plan, ordinal=1, evaluated_at=step1_window_start)
    with pytest.raises(PaperPersistenceIntegrityError):
        store.evaluate_step(
            step=step1.step,
            plan=accepted_close.plan,
            worker_identity="w1",
            evaluated_at=step1_window_start,
            mark_observations=(second_mark,),
        )

    with sqlite3.connect(path) as connection:
        applications_after = connection.execute(
            "SELECT COUNT(*) FROM live_paper_position_fill_applications"
        ).fetchone()[0]
        releases_after = connection.execute(
            "SELECT COUNT(*) FROM live_paper_reservation_releases"
        ).fetchone()[0]
    assert applications_after == applications_before
    assert releases_after == releases_before


def test_release_quantity_not_matching_remainder_conflicts_with_forged_row(
    tmp_path: Path,
) -> None:
    from datetime import timedelta

    path = tmp_path / "live.sqlite"
    store = SQLitePaperStore(path)
    account = bootstrap()
    mark = observation()
    store.append_market_observations((mark,))
    position_id = _open_entry_position(store, account, quantity=Decimal("500"), mark=mark)

    close_intent = synthetic_close_intent(
        position_id=PositionId(position_id), quantity=Decimal("500")
    )
    insert_m2d_intent(path, close_intent)
    close_policy = fill_policy(maximum_steps=1, maximum_market_age=timedelta(seconds=1))
    accepted_close = store.accept_ordinary_close_order(
        fill_policy=close_policy,
        account_bootstrap=account,
        intent=close_intent,
        evaluated_at=NOW,
    )
    step = store.create_step(plan=accepted_close.plan, ordinal=0, evaluated_at=NOW)

    with sqlite3.connect(path) as connection:
        order_events_before = connection.execute(
            "SELECT COUNT(*) FROM live_paper_order_events WHERE paper_order_id = ?",
            (accepted_close.order.paper_order_id,),
        ).fetchone()[0]
        # A forged release for this order carrying a quantity that is not the true
        # remainder (500), pre-empting the correctly-computed one this NO_MARKET
        # terminal resolution is about to write; T3C never writes a consumption, so
        # this isolates the release-quantity guarantee from the consumption-guard
        # cases above.
        connection.execute(
            "INSERT INTO live_paper_reservation_releases "
            "(release_id, contract_version, close_intent_idempotency_key, "
            "paper_order_id, terminal_order_state, released_quantity, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "forged-release-wrong-quantity",
                "paper-reservation-release-v1",
                close_intent.idempotency_key,
                accepted_close.order.paper_order_id,
                "CANCELLED",
                "999",
                NOW.isoformat(),
            ),
        )

    with pytest.raises(PaperPersistenceConflict):
        store.evaluate_step(
            step=step.step,
            plan=accepted_close.plan,
            worker_identity="w1",
            evaluated_at=step.step.evaluation_due_at + timedelta(seconds=1),
        )

    with sqlite3.connect(path) as connection:
        release_count = connection.execute(
            "SELECT COUNT(*) FROM live_paper_reservation_releases"
        ).fetchone()[0]
        order_events_after = connection.execute(
            "SELECT COUNT(*) FROM live_paper_order_events WHERE paper_order_id = ?",
            (accepted_close.order.paper_order_id,),
        ).fetchone()[0]
    assert release_count == 1
    # The terminal REJECTED event never commits; the count is unchanged.
    assert order_events_after == order_events_before


def test_reduce_only_attachment_rejects_missing_position_row(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    store = SQLitePaperStore(path)
    account = bootstrap()
    close_intent = synthetic_close_intent(
        position_id=PositionId("paper-position-does-not-exist"),
        quantity=Decimal("100"),
    )
    insert_m2d_intent(path, close_intent)
    with pytest.raises(PaperPersistenceIntegrityError):
        store.accept_ordinary_close_order(
            fill_policy=fill_policy(),
            account_bootstrap=account,
            intent=close_intent,
            evaluated_at=NOW,
        )
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM live_paper_orders").fetchone()[0] == 0


def test_reduce_only_attachment_rejects_pair_mismatch(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    store = SQLitePaperStore(path)
    account = bootstrap()
    mark = observation()
    store.append_market_observations((mark,))
    position_id = _open_entry_position(store, account, quantity=Decimal("500"), mark=mark)

    close_intent = synthetic_close_intent(
        position_id=PositionId(position_id),
        pair=OTHER_PAIR,
        quantity=Decimal("500"),
    )
    insert_m2d_intent(path, close_intent)
    with pytest.raises(PaperPersistenceIntegrityError, match="Pair"):
        store.accept_ordinary_close_order(
            fill_policy=fill_policy(),
            account_bootstrap=account,
            intent=close_intent,
            evaluated_at=NOW,
        )
    with sqlite3.connect(path) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM live_paper_ledger_entries").fetchone()[0] == 0
        )


def test_reduce_only_attachment_rejects_account_mismatch(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    store = SQLitePaperStore(path)
    account_one = bootstrap(initial_cash=Decimal("1000000"))
    account_two = bootstrap(initial_cash=Decimal("2000000"))
    mark = observation()
    store.append_market_observations((mark,))
    position_id = _open_entry_position(store, account_one, quantity=Decimal("500"), mark=mark)

    close_intent = synthetic_close_intent(
        position_id=PositionId(position_id), quantity=Decimal("500")
    )
    insert_m2d_intent(path, close_intent)
    with pytest.raises(PaperPersistenceIntegrityError, match="account"):
        store.accept_ordinary_close_order(
            fill_policy=fill_policy(),
            account_bootstrap=account_two,
            intent=close_intent,
            evaluated_at=NOW,
        )


def test_reduce_only_attachment_rejects_side_mismatch(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    store = SQLitePaperStore(path)
    account = bootstrap()
    mark = observation()
    store.append_market_observations((mark,))
    position_id = _open_entry_position(store, account, quantity=Decimal("500"), mark=mark)

    # A LONG position (entered BUY) can only be reduced by a SELL close; supplying BUY
    # is the Side-rule violation.
    close_intent = synthetic_close_intent(
        position_id=PositionId(position_id),
        side=Side.BUY,
        quantity=Decimal("500"),
    )
    insert_m2d_intent(path, close_intent)
    with pytest.raises(PaperPersistenceIntegrityError, match="Side"):
        store.accept_ordinary_close_order(
            fill_policy=fill_policy(),
            account_bootstrap=account,
            intent=close_intent,
            evaluated_at=NOW,
        )


def test_authority_mismatch_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    store = SQLitePaperStore(path)
    account = bootstrap()
    mark = observation()
    store.append_market_observations((mark,))
    position_id = _open_entry_position(store, account, quantity=Decimal("500"), mark=mark)

    close_intent = synthetic_close_intent(
        position_id=PositionId(position_id),
        quantity=Decimal("500"),
        authority=ExecutionAuthorityMode.SHADOW_NOT_SUBMITTED,
    )
    with pytest.raises(PaperPersistenceIntegrityError):
        store.accept_ordinary_close_order(
            fill_policy=fill_policy(),
            account_bootstrap=account,
            intent=close_intent,
            evaluated_at=NOW,
        )


def test_close_intent_with_no_persisted_m2d_row_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    store = SQLitePaperStore(path)
    account = bootstrap()
    mark = observation()
    store.append_market_observations((mark,))
    position_id = _open_entry_position(store, account, quantity=Decimal("500"), mark=mark)

    close_intent = synthetic_close_intent(
        position_id=PositionId(position_id), quantity=Decimal("500")
    )
    # Deliberately never inserted into live_ordinary_close_approved_intents.
    with pytest.raises(PaperPersistenceIntegrityError):
        store.accept_ordinary_close_order(
            fill_policy=fill_policy(),
            account_bootstrap=account,
            intent=close_intent,
            evaluated_at=NOW,
        )


def test_close_intent_content_mismatch_against_persisted_m2d_row_fails_closed(
    tmp_path: Path,
) -> None:
    path = tmp_path / "live.sqlite"
    store = SQLitePaperStore(path)
    account = bootstrap()
    mark = observation()
    store.append_market_observations((mark,))
    position_id = _open_entry_position(store, account, quantity=Decimal("500"), mark=mark)

    close_intent = synthetic_close_intent(
        position_id=PositionId(position_id), quantity=Decimal("500")
    )
    # Persist a row under the exact same idempotency_key but different quantity, as if
    # M2-D's own persisted content had diverged from the supplied intent object.
    forged = synthetic_close_intent(position_id=PositionId(position_id), quantity=Decimal("999"))
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO live_ordinary_close_risk_decisions "
            "(risk_decision_id, portfolio_decision_id, risk_policy_version, "
            "maximum_capacity_age_us, outcome, reason) VALUES (?, ?, ?, ?, 'APPROVE', 'APPROVED')",
            (forged.risk_decision_id, forged.portfolio_decision_id, "risk-v1", 3_600_000_000),
        )
        connection.execute(
            "INSERT INTO live_ordinary_close_approved_intents "
            "(close_candidate_id, portfolio_decision_id, risk_decision_id, capacity_evidence_id, "
            "position_id, pair, side, quantity, authority, idempotency_key, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                forged.close_candidate_id,
                forged.portfolio_decision_id,
                forged.risk_decision_id,
                forged.capacity_evidence_id,
                forged.position_id.value,
                forged.pair.symbol,
                forged.side.value,
                str(forged.quantity),
                forged.authority.value,
                close_intent.idempotency_key,
                forged.created_at.isoformat(),
            ),
        )
    # A corrupted M2-D row fails its own self-consistency check before the supplied
    # intent is even compared, so a plain ValueError (PaperPersistenceIntegrityError's
    # own base class) is the frozen fail-closed outcome here.
    with pytest.raises(ValueError, match="does not match content"):
        store.accept_ordinary_close_order(
            fill_policy=fill_policy(),
            account_bootstrap=account,
            intent=close_intent,
            evaluated_at=NOW,
        )
    with sqlite3.connect(path) as connection:
        # Only the earlier entry order exists; the close order is never written.
        assert connection.execute("SELECT COUNT(*) FROM live_paper_orders").fetchone()[0] == 1


def test_entry_and_liquidation_orders_create_zero_reservation_rows(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    store = SQLitePaperStore(path)
    account = bootstrap()
    mark = observation()
    store.append_market_observations((mark,))
    position_id = _open_entry_position(store, account, quantity=Decimal("500"), mark=mark)

    from swap_bot.models import ApprovedLiquidationIntent

    liq_intent = ApprovedLiquidationIntent(
        ExecutionIntentId("liq-1"),
        RiskDecisionId("risk-liq-1"),
        PositionId(position_id),
        PAIR,
        Decimal("500"),
        "liq-idem-1",
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
    result = store.evaluate_step(
        step=step.step,
        plan=accepted_liq.plan,
        worker_identity="w1",
        evaluated_at=NOW,
        mark_observations=(mark,),
    )
    assert result.reservation_consumption is None
    assert result.reservation_release is None
    with sqlite3.connect(path) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM live_paper_reservation_consumptions"
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute("SELECT COUNT(*) FROM live_paper_reservation_releases").fetchone()[0]
            == 0
        )
