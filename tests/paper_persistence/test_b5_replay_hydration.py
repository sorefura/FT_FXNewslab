"""Coverage for the two read-only store methods B5 uses to skip re-submitting T1
(order acceptance) or a Step's own create_step call for an intent/Step that is
already persisted, instead of racing that call's fresh evaluated_at against the
first-write audit column already on file (see application.py's _accept_or_reuse
and _advance)."""

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


# ---------------------------------------------------------------------------
# hydrate_accepted_order
# ---------------------------------------------------------------------------


def test_hydrate_accepted_order_returns_none_when_not_persisted(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    store = SQLitePaperStore(path)

    assert store.hydrate_accepted_order(paper_order_id="paper-order-does-not-exist") is None


def test_hydrate_accepted_order_returns_the_persisted_accepted_order(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    store = SQLitePaperStore(path)
    accepted = _accept(store, fill_policy(maximum_steps=3))

    hydrated = store.hydrate_accepted_order(paper_order_id=accepted.order.paper_order_id)

    assert hydrated is not None
    assert hydrated.order == accepted.order
    assert hydrated.accepted_event == accepted.accepted_event
    assert hydrated.plan == accepted.plan


def test_hydrate_accepted_order_is_read_only(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    store = SQLitePaperStore(path)
    accepted = _accept(store, fill_policy(maximum_steps=3))
    before = _counts(path)

    for _ in range(3):
        store.hydrate_accepted_order(paper_order_id=accepted.order.paper_order_id)
        store.hydrate_accepted_order(paper_order_id="paper-order-does-not-exist")

    assert _counts(path) == before


def test_hydrate_accepted_order_rejects_blank_id(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    store = SQLitePaperStore(path)

    with pytest.raises(ValueError):
        store.hydrate_accepted_order(paper_order_id="")


def test_hydrate_accepted_order_rejects_non_str_id(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    store = SQLitePaperStore(path)

    with pytest.raises(ValueError):
        store.hydrate_accepted_order(paper_order_id=None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# hydrate_created_step
# ---------------------------------------------------------------------------


def test_hydrate_created_step_returns_none_when_step_not_persisted(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    store = SQLitePaperStore(path)
    accepted = _accept(store, fill_policy(maximum_steps=3))

    assert store.hydrate_created_step(plan=accepted.plan, ordinal=0) is None


def test_hydrate_created_step_returns_ordinal_zero_with_its_open_event(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    store = SQLitePaperStore(path)
    accepted = _accept(store, fill_policy(maximum_steps=3))
    created = store.create_step(plan=accepted.plan, ordinal=0, evaluated_at=NOW)

    hydrated = store.hydrate_created_step(plan=accepted.plan, ordinal=0)

    assert hydrated is not None
    assert hydrated.step == created.step
    assert hydrated.open_event == created.open_event
    assert hydrated.open_event is not None


def test_hydrate_created_step_returns_later_ordinal_with_no_open_event(tmp_path: Path) -> None:
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
    step1_window_start = step0.step.evaluation_due_at + policy.step_gap
    created1 = store.create_step(plan=accepted.plan, ordinal=1, evaluated_at=step1_window_start)

    hydrated = store.hydrate_created_step(plan=accepted.plan, ordinal=1)

    assert hydrated is not None
    assert hydrated.step == created1.step
    assert hydrated.open_event is None


def test_hydrate_created_step_lets_a_pending_step_resume_with_a_later_evaluated_at(
    tmp_path: Path,
) -> None:
    """The scenario create_step's own audit-only created_at cannot tolerate on a
    second call: the exact reachability gap this fix closes at the B5 seam."""
    path = tmp_path / "live.sqlite"
    store = SQLitePaperStore(path)
    no_obs_pair_policy = fill_policy(maximum_steps=1, step_window_duration=timedelta(minutes=1))
    accepted = _accept(store, no_obs_pair_policy)
    created = store.create_step(plan=accepted.plan, ordinal=0, evaluated_at=NOW)
    pending = store.evaluate_step(
        step=created.step, plan=accepted.plan, worker_identity="w1", evaluated_at=NOW
    )
    assert pending.outcome is StepResolutionOutcome.PENDING

    resumed = store.hydrate_created_step(plan=accepted.plan, ordinal=0)
    assert resumed is not None

    later = created.step.evaluation_due_at + timedelta(seconds=1)
    resolved = store.evaluate_step(
        step=resumed.step, plan=accepted.plan, worker_identity="w1", evaluated_at=later
    )

    assert resolved.outcome is StepResolutionOutcome.T3C
    assert resolved.order_events[-1].state is PaperOrderState.REJECTED


def test_hydrate_created_step_is_read_only(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    store = SQLitePaperStore(path)
    accepted = _accept(store, fill_policy(maximum_steps=3))
    store.create_step(plan=accepted.plan, ordinal=0, evaluated_at=NOW)
    before = _counts(path)

    for _ in range(3):
        store.hydrate_created_step(plan=accepted.plan, ordinal=0)
        store.hydrate_created_step(plan=accepted.plan, ordinal=5)

    assert _counts(path) == before


def test_hydrate_created_step_wrong_plan_type_raises_type_error(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    store = SQLitePaperStore(path)

    with pytest.raises(TypeError):
        store.hydrate_created_step(plan=object(), ordinal=0)  # type: ignore[arg-type]


def test_hydrate_created_step_negative_ordinal_raises_value_error(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    store = SQLitePaperStore(path)
    accepted = _accept(store, fill_policy(maximum_steps=3))

    with pytest.raises(ValueError):
        store.hydrate_created_step(plan=accepted.plan, ordinal=-1)


def test_hydrate_created_step_unpersisted_plan_raises_integrity_error(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    store = SQLitePaperStore(path)
    other_store = SQLitePaperStore(tmp_path / "other.sqlite")
    foreign = _accept(other_store, fill_policy(maximum_steps=3))

    with pytest.raises(PaperPersistenceIntegrityError):
        store.hydrate_created_step(plan=foreign.plan, ordinal=0)
