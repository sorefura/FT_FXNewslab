import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from swap_bot.paper import (
    EvaluatedStep,
    PaperOrderState,
    PaperPartialFillMode,
    PaperPersistenceIntegrityError,
    PaperStepResolutionVariant,
    SQLitePaperStore,
    StepResolutionOutcome,
)

from tests.paper_persistence._helpers import (
    NOW,
    PAIR,
    bootstrap,
    execution_intent,
    fill_policy,
    observation,
)


def _counts(path: Path) -> dict[str, int]:
    with sqlite3.connect(path) as connection:
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'live_paper_%'"
            )
        ]
        return {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in tables
        }


def test_fresh_entry_flow_inserts_and_exact_replay_adds_zero_rows(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    store = SQLitePaperStore(path)
    account = bootstrap()
    policy = fill_policy(maximum_steps=1)
    mark = observation()
    store.append_market_observations((mark,))
    intent = execution_intent()

    accepted = store.accept_entry_order(
        fill_policy=policy, account_bootstrap=account, intent=intent, evaluated_at=NOW
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
    assert result.fill is not None and result.fill.fill_quantity == Decimal("1000")

    before = _counts(path)

    # Exact replay: same order/plan/step, same evaluate_step call.
    replayed_accepted = store.accept_entry_order(
        fill_policy=policy, account_bootstrap=account, intent=intent, evaluated_at=NOW
    )
    replayed_step = store.create_step(plan=accepted.plan, ordinal=0, evaluated_at=NOW)
    replayed_result = store.evaluate_step(
        step=step.step,
        plan=accepted.plan,
        worker_identity="w1",
        evaluated_at=NOW,
        mark_observations=(mark,),
    )
    after = _counts(path)

    assert replayed_accepted.order.paper_order_id == accepted.order.paper_order_id
    assert replayed_step.step.fill_evaluation_step_id == step.step.fill_evaluation_step_id
    assert replayed_result.fill is not None
    assert replayed_result.fill.paper_fill_id == result.fill.paper_fill_id
    assert before == after


def test_two_step_partial_fill_reconstructs_remaining_quantity_exactly(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    store = SQLitePaperStore(path)
    account = bootstrap()
    policy = fill_policy(
        maximum_steps=2,
        partial_fill_mode=PaperPartialFillMode.FRACTION_OF_REMAINING,
        partial_fill_fraction=Decimal("0.4"),
    )
    mark = observation()
    store.append_market_observations((mark,))
    intent = execution_intent(quantity=Decimal("1000"))

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
    assert result0.fill.fill_quantity == Decimal("400.0")
    assert result0.fill.remaining_quantity_after == Decimal("600.0")

    # Step 1's window starts only after Step 0's due boundary plus step_gap, so a
    # second, distinct observation and a later evaluated_at are both required.
    step1_window_start = step0.step.evaluation_due_at + policy.step_gap
    second_mark = observation(
        received_at=step1_window_start, provider_observed_at=step1_window_start
    )
    store.append_market_observations((second_mark,))

    step1 = store.create_step(plan=accepted.plan, ordinal=1, evaluated_at=step1_window_start)
    assert step1.step.remaining_quantity_before == Decimal("600.0")
    result1 = store.evaluate_step(
        step=step1.step,
        plan=accepted.plan,
        worker_identity="w1",
        evaluated_at=step1_window_start,
        mark_observations=(second_mark,),
    )
    assert result1.outcome is StepResolutionOutcome.T3B
    assert result1.fill.fill_quantity == Decimal("240.0")
    assert len(result1.order_events) == 2
    assert result1.order_events[0].state is PaperOrderState.PARTIALLY_FILLED
    assert result1.order_events[1].state is PaperOrderState.CANCELLED


def test_pending_attempt_then_resolves_with_a_pre_due_eligible_quote(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    store = SQLitePaperStore(path)
    account = bootstrap()
    policy = fill_policy(maximum_steps=1)
    intent = execution_intent()

    accepted = store.accept_entry_order(
        fill_policy=policy, account_bootstrap=account, intent=intent, evaluated_at=NOW
    )
    step = store.create_step(plan=accepted.plan, ordinal=0, evaluated_at=NOW)

    pending = store.evaluate_step(
        step=step.step,
        plan=accepted.plan,
        worker_identity="w1",
        evaluated_at=NOW,
    )
    assert pending.outcome is StepResolutionOutcome.PENDING
    assert pending.attempt is not None

    mark = observation()
    store.append_market_observations((mark,))
    resolved = store.evaluate_step(
        step=step.step,
        plan=accepted.plan,
        worker_identity="w1",
        evaluated_at=NOW + timedelta(seconds=1),
        mark_observations=(mark,),
    )
    assert resolved.outcome is StepResolutionOutcome.T3A
    assert resolved.fill is not None


def test_no_market_terminal_after_due_rejects_no_order(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    store = SQLitePaperStore(path)
    account = bootstrap()
    policy = fill_policy(maximum_steps=1)
    intent = execution_intent()

    accepted = store.accept_entry_order(
        fill_policy=policy, account_bootstrap=account, intent=intent, evaluated_at=NOW
    )
    step = store.create_step(plan=accepted.plan, ordinal=0, evaluated_at=NOW)
    due = step.step.evaluation_due_at

    result = store.evaluate_step(
        step=step.step,
        plan=accepted.plan,
        worker_identity="w1",
        evaluated_at=due + timedelta(seconds=1),
    )
    assert result.outcome is StepResolutionOutcome.T3C
    assert result.no_market_outcome is not None
    assert result.order_events[0].state is PaperOrderState.REJECTED

    # Replay with a newly supplied eligible observation must not create a selection or Fill.
    mark = observation(received_at=NOW, provider_observed_at=NOW)
    store.append_market_observations((mark,))
    replayed = store.evaluate_step(
        step=step.step,
        plan=accepted.plan,
        worker_identity="w1",
        evaluated_at=due + timedelta(seconds=1),
        mark_observations=(mark,),
    )
    assert replayed.outcome is StepResolutionOutcome.T3C
    assert replayed.selection is None
    assert replayed.fill is None


def test_create_step_rejects_missing_plan_parent(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    store = SQLitePaperStore(path)
    account = bootstrap()
    policy = fill_policy()
    intent = execution_intent()
    accepted = store.accept_entry_order(
        fill_policy=policy, account_bootstrap=account, intent=intent, evaluated_at=NOW
    )

    other_path = tmp_path / "other.sqlite"
    other_store = SQLitePaperStore(other_path)
    with pytest.raises(PaperPersistenceIntegrityError):
        other_store.create_step(plan=accepted.plan, ordinal=0, evaluated_at=NOW)


def test_evaluate_step_rejects_a_tampered_step_content(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    store = SQLitePaperStore(path)
    account = bootstrap()
    policy = fill_policy()
    intent = execution_intent()
    accepted = store.accept_entry_order(
        fill_policy=policy, account_bootstrap=account, intent=intent, evaluated_at=NOW
    )
    step = store.create_step(plan=accepted.plan, ordinal=0, evaluated_at=NOW)
    # A forged object bypassing __post_init__'s own recomputation (which a genuine
    # caller can never construct) fails closed before any persisted-content comparison.
    tampered = step.step
    object.__setattr__(tampered, "remaining_quantity_before", Decimal("1"))
    with pytest.raises(ValueError, match="does not match content"):
        store.evaluate_step(
            step=tampered, plan=accepted.plan, worker_identity="w1", evaluated_at=NOW
        )


def test_second_fill_for_one_selection_is_rejected_by_the_schema(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    store = SQLitePaperStore(path)
    account = bootstrap()
    policy = fill_policy(maximum_steps=1)
    mark = observation()
    store.append_market_observations((mark,))
    intent = execution_intent()
    accepted = store.accept_entry_order(
        fill_policy=policy, account_bootstrap=account, intent=intent, evaluated_at=NOW
    )
    step = store.create_step(plan=accepted.plan, ordinal=0, evaluated_at=NOW)
    result = store.evaluate_step(
        step=step.step,
        plan=accepted.plan,
        worker_identity="w1",
        evaluated_at=NOW,
        mark_observations=(mark,),
    )
    assert result.fill is not None

    with sqlite3.connect(path) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO live_paper_fills "
                "(paper_fill_id, fill_contract_version, fill_evaluation_step_id, "
                "market_observation_selection_id, market_observation_id, pair, side, "
                "fill_quantity, fill_price, reference_price, slippage_basis_points, "
                "fill_model_version, remaining_quantity_before, remaining_quantity_after, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "paper-fill-forged",
                    result.fill.fill_contract_version,
                    result.fill.fill_evaluation_step_id,
                    result.fill.market_observation_selection_id,
                    result.fill.market_observation_id,
                    PAIR.symbol,
                    "BUY",
                    "1",
                    "150",
                    "150",
                    "0",
                    "fill-v1",
                    "1000",
                    "999",
                    NOW.isoformat(),
                ),
            )


def test_concurrent_identical_writers_converge_on_one_order_plan_and_step(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    store = SQLitePaperStore(path)
    account = bootstrap()
    policy = fill_policy(maximum_steps=1)
    intent = execution_intent()

    def _accept_and_create_step() -> tuple[str, str]:
        accepted = store.accept_entry_order(
            fill_policy=policy, account_bootstrap=account, intent=intent, evaluated_at=NOW
        )
        step = store.create_step(plan=accepted.plan, ordinal=0, evaluated_at=NOW)
        return accepted.order.paper_order_id, step.step.fill_evaluation_step_id

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = tuple(executor.map(lambda _: _accept_and_create_step(), range(4)))

    assert len({order_id for order_id, _ in results}) == 1
    assert len({step_id for _, step_id in results}) == 1
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM live_paper_orders").fetchone()[0] == 1
        assert (
            connection.execute("SELECT COUNT(*) FROM live_paper_fill_evaluation_plans").fetchone()[
                0
            ]
            == 1
        )
        assert (
            connection.execute("SELECT COUNT(*) FROM live_paper_fill_evaluation_steps").fetchone()[
                0
            ]
            == 1
        )


def test_concurrent_distinct_step_resolutions_yield_one_terminal_claim(tmp_path: Path) -> None:
    # Two real threads race to resolve the same Step. One racer's own view of the
    # world (no eligible quote yet) would terminate NO_MARKET; the other's (the
    # quote already landed) would terminate MARKET_SELECTED. Because every T3
    # transaction takes BEGIN IMMEDIATE and re-checks the terminal claim before
    # computing anything, only the transaction that actually wins the database
    # write lock computes a fresh resolution; the loser's own distinct attempt
    # never reaches the insert path and instead re-hydrates and returns the
    # winner's persisted claim, so both racers succeed and agree.
    path = tmp_path / "live.sqlite"
    store = SQLitePaperStore(path)
    account = bootstrap()
    policy = fill_policy(maximum_steps=1)
    intent = execution_intent()

    accepted = store.accept_entry_order(
        fill_policy=policy, account_bootstrap=account, intent=intent, evaluated_at=NOW
    )
    step = store.create_step(plan=accepted.plan, ordinal=0, evaluated_at=NOW)
    due = step.step.evaluation_due_at
    mark = observation(received_at=due, provider_observed_at=due)

    barrier = threading.Barrier(3)
    results: dict[str, EvaluatedStep] = {}
    errors: dict[str, BaseException] = {}

    def _publish_observation() -> None:
        barrier.wait()
        store.append_market_observations((mark,))

    def _evaluate(worker_identity: str) -> None:
        barrier.wait()
        try:
            results[worker_identity] = store.evaluate_step(
                step=step.step,
                plan=accepted.plan,
                worker_identity=worker_identity,
                evaluated_at=due,
                mark_observations=(mark,),
            )
        except BaseException as exc:  # noqa: BLE001 - captured for assertion below
            errors[worker_identity] = exc

    threads = [
        threading.Thread(target=_publish_observation),
        threading.Thread(target=_evaluate, args=("worker-a",)),
        threading.Thread(target=_evaluate, args=("worker-b",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors, f"a racing evaluate_step call failed unexpectedly: {errors}"
    assert set(results) == {"worker-a", "worker-b"}

    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        claims = connection.execute(
            "SELECT * FROM live_paper_step_terminal_claims WHERE fill_evaluation_step_id = ?",
            (step.step.fill_evaluation_step_id,),
        ).fetchall()
        assert len(claims) == 1
        claim = claims[0]
        selection_count = connection.execute(
            "SELECT COUNT(*) FROM live_paper_market_observation_selections "
            "WHERE fill_evaluation_step_id = ?",
            (step.step.fill_evaluation_step_id,),
        ).fetchone()[0]
        no_market_count = connection.execute(
            "SELECT COUNT(*) FROM live_paper_no_market_outcomes "
            "WHERE fill_evaluation_step_id = ?",
            (step.step.fill_evaluation_step_id,),
        ).fetchone()[0]
        fill_count = connection.execute(
            "SELECT COUNT(*) FROM live_paper_fills WHERE fill_evaluation_step_id = ?",
            (step.step.fill_evaluation_step_id,),
        ).fetchone()[0]

    result_a, result_b = results["worker-a"], results["worker-b"]
    variant = PaperStepResolutionVariant(claim["variant"])

    if variant is PaperStepResolutionVariant.MARKET_SELECTED:
        assert (selection_count, no_market_count, fill_count) == (1, 0, 1)
        assert result_a.selection is not None and result_b.selection is not None
        assert (
            result_a.selection.market_observation_selection_id
            == result_b.selection.market_observation_selection_id
            == claim["resolution_id"]
        )
        assert result_a.fill is not None and result_b.fill is not None
        assert result_a.fill.paper_fill_id == result_b.fill.paper_fill_id
    else:
        assert (selection_count, no_market_count, fill_count) == (0, 1, 0)
        assert result_a.no_market_outcome is not None and result_b.no_market_outcome is not None
        assert (
            result_a.no_market_outcome.no_market_outcome_id
            == result_b.no_market_outcome.no_market_outcome_id
            == claim["resolution_id"]
        )
