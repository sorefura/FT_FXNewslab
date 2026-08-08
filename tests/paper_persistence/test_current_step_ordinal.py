from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from swap_bot.paper import (
    PaperOrderState,
    PaperPartialFillMode,
    PaperPersistenceIntegrityError,
    SQLitePaperStore,
    StepResolutionOutcome,
)

from tests.paper_persistence._helpers import (
    NOW,
    bootstrap,
    execution_intent,
    fill_policy,
    observation,
)
from tests.paper_persistence.test_entry_flow import _counts


def _accept(store: SQLitePaperStore, policy, *, idempotency_key: str = "entry-idem-1"):
    return store.accept_entry_order(
        fill_policy=policy,
        account_bootstrap=bootstrap(),
        intent=execution_intent(idempotency_key=idempotency_key),
        evaluated_at=NOW,
    )


def test_no_step_returns_ordinal_zero(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    store = SQLitePaperStore(path)
    accepted = _accept(store, fill_policy(maximum_steps=3))

    assert store.current_step_ordinal(plan=accepted.plan) == 0


def test_unresolved_step_zero_is_reused(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    store = SQLitePaperStore(path)
    accepted = _accept(store, fill_policy(maximum_steps=3))
    step0 = store.create_step(plan=accepted.plan, ordinal=0, evaluated_at=NOW)
    pending = store.evaluate_step(
        step=step0.step, plan=accepted.plan, worker_identity="w1", evaluated_at=NOW
    )
    assert pending.outcome is StepResolutionOutcome.PENDING

    assert store.current_step_ordinal(plan=accepted.plan) == 0


def test_legitimate_continuation_returns_next_ordinal(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    store = SQLitePaperStore(path)
    policy = fill_policy(
        maximum_steps=3,
        partial_fill_mode=PaperPartialFillMode.FRACTION_OF_REMAINING,
        partial_fill_fraction=Decimal("0.4"),
    )
    accepted = _accept(store, policy)
    mark = observation()
    store.append_market_observations((mark,))
    step0 = store.create_step(plan=accepted.plan, ordinal=0, evaluated_at=NOW)
    result0 = store.evaluate_step(
        step=step0.step,
        plan=accepted.plan,
        worker_identity="w1",
        evaluated_at=NOW,
        mark_observations=(mark,),
    )
    assert result0.outcome is StepResolutionOutcome.T3A
    assert result0.fill.remaining_quantity_after > 0

    assert store.current_step_ordinal(plan=accepted.plan) == 1


def test_unresolved_step_one_is_reused(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    store = SQLitePaperStore(path)
    policy = fill_policy(
        maximum_steps=3,
        partial_fill_mode=PaperPartialFillMode.FRACTION_OF_REMAINING,
        partial_fill_fraction=Decimal("0.4"),
    )
    accepted = _accept(store, policy)
    mark = observation()
    store.append_market_observations((mark,))
    step0 = store.create_step(plan=accepted.plan, ordinal=0, evaluated_at=NOW)
    store.evaluate_step(
        step=step0.step,
        plan=accepted.plan,
        worker_identity="w1",
        evaluated_at=NOW,
        mark_observations=(mark,),
    )
    step1_window_start = step0.step.evaluation_due_at + policy.step_gap
    step1 = store.create_step(plan=accepted.plan, ordinal=1, evaluated_at=step1_window_start)
    pending1 = store.evaluate_step(
        step=step1.step, plan=accepted.plan, worker_identity="w1", evaluated_at=step1_window_start
    )
    assert pending1.outcome is StepResolutionOutcome.PENDING

    assert store.current_step_ordinal(plan=accepted.plan) == 1


def test_filled_step_is_not_a_legitimate_continuation(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    store = SQLitePaperStore(path)
    policy = fill_policy(maximum_steps=3, partial_fill_mode=PaperPartialFillMode.FULL_REMAINING)
    accepted = _accept(store, policy)
    mark = observation()
    store.append_market_observations((mark,))
    step0 = store.create_step(plan=accepted.plan, ordinal=0, evaluated_at=NOW)
    result0 = store.evaluate_step(
        step=step0.step,
        plan=accepted.plan,
        worker_identity="w1",
        evaluated_at=NOW,
        mark_observations=(mark,),
    )
    assert result0.fill.remaining_quantity_after == 0

    assert store.current_step_ordinal(plan=accepted.plan) == 0


def test_no_market_terminal_is_not_a_legitimate_continuation(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    store = SQLitePaperStore(path)
    policy = fill_policy(maximum_steps=3)
    accepted = _accept(store, policy)
    step0 = store.create_step(plan=accepted.plan, ordinal=0, evaluated_at=NOW)
    due = step0.step.evaluation_due_at
    result0 = store.evaluate_step(
        step=step0.step,
        plan=accepted.plan,
        worker_identity="w1",
        evaluated_at=due + timedelta(seconds=1),
    )
    assert result0.outcome is StepResolutionOutcome.T3C

    assert store.current_step_ordinal(plan=accepted.plan) == 0


def test_maximum_steps_reached_is_not_a_legitimate_continuation(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    store = SQLitePaperStore(path)
    policy = fill_policy(
        maximum_steps=2,
        partial_fill_mode=PaperPartialFillMode.FRACTION_OF_REMAINING,
        partial_fill_fraction=Decimal("0.4"),
    )
    accepted = _accept(store, policy)
    mark = observation()
    store.append_market_observations((mark,))
    step0 = store.create_step(plan=accepted.plan, ordinal=0, evaluated_at=NOW)
    store.evaluate_step(
        step=step0.step,
        plan=accepted.plan,
        worker_identity="w1",
        evaluated_at=NOW,
        mark_observations=(mark,),
    )
    step1_window_start = step0.step.evaluation_due_at + policy.step_gap
    second_mark = observation(
        received_at=step1_window_start, provider_observed_at=step1_window_start
    )
    store.append_market_observations((second_mark,))
    step1 = store.create_step(plan=accepted.plan, ordinal=1, evaluated_at=step1_window_start)
    result1 = store.evaluate_step(
        step=step1.step,
        plan=accepted.plan,
        worker_identity="w1",
        evaluated_at=step1_window_start,
        mark_observations=(second_mark,),
    )
    assert result1.outcome is StepResolutionOutcome.T3B
    assert result1.fill.remaining_quantity_after > 0
    assert result1.order_events[-1].state is PaperOrderState.CANCELLED

    assert store.current_step_ordinal(plan=accepted.plan) == 1


def test_current_step_ordinal_is_read_only(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    store = SQLitePaperStore(path)
    policy = fill_policy(
        maximum_steps=3,
        partial_fill_mode=PaperPartialFillMode.FRACTION_OF_REMAINING,
        partial_fill_fraction=Decimal("0.4"),
    )
    accepted = _accept(store, policy)
    mark = observation()
    store.append_market_observations((mark,))
    step0 = store.create_step(plan=accepted.plan, ordinal=0, evaluated_at=NOW)
    store.evaluate_step(
        step=step0.step,
        plan=accepted.plan,
        worker_identity="w1",
        evaluated_at=NOW,
        mark_observations=(mark,),
    )
    before = _counts(path)

    for _ in range(3):
        store.current_step_ordinal(plan=accepted.plan)

    assert _counts(path) == before


def test_wrong_plan_type_raises_type_error(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    store = SQLitePaperStore(path)

    with pytest.raises(TypeError):
        store.current_step_ordinal(plan=object())  # type: ignore[arg-type]


def test_unpersisted_plan_raises_integrity_error(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    store = SQLitePaperStore(path)
    other_store = SQLitePaperStore(tmp_path / "other.sqlite")
    foreign = _accept(other_store, fill_policy(maximum_steps=3))

    with pytest.raises(PaperPersistenceIntegrityError):
        store.current_step_ordinal(plan=foreign.plan)
