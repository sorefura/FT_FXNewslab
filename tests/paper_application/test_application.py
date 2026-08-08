import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from fx_core import CurrencyPair
from swap_bot.execution import ExecutionService, GmoPrivatePostTransport, LiveArmPolicy
from swap_bot.execution_authority import ExecutionAuthorityMode
from swap_bot.models import (
    ApprovedExecutionIntent,
    CandidateId,
    ExecutionIntentId,
    PositionId,
    RiskDecisionId,
    Side,
)
from swap_bot.paper import (
    PaperApplicationDisposition,
    PaperApplicationResult,
    PaperApplicationService,
    PaperOrderState,
    PaperPartialFillMode,
    PaperPersistenceConflict,
    SQLitePaperStore,
)

from tests.paper_persistence._helpers import (
    NOW,
    bootstrap,
    execution_intent,
    fill_policy,
    insert_m2d_intent,
    liquidation_intent,
    observation,
    synthetic_close_intent,
)

# ---------------------------------------------------------------------------
# Clock doubles
# ---------------------------------------------------------------------------


class _FixedClock:
    def __init__(self, value: object) -> None:
        self.value = value
        self.calls = 0

    def now(self) -> object:
        self.calls += 1
        return self.value


class _SequenceClock:
    def __init__(self, values: list) -> None:
        self._values = list(values)
        self.calls = 0

    def now(self) -> object:
        value = self._values[self.calls]
        self.calls += 1
        return value


# ---------------------------------------------------------------------------
# Duck-typed / subclass intent doubles (structurally identical, not exact type)
# ---------------------------------------------------------------------------


class _ExecutionIntentSubclass(ApprovedExecutionIntent):
    pass


@dataclass(frozen=True, slots=True)
class _DuckExecutionIntent:
    intent_id: ExecutionIntentId
    candidate_id: CandidateId
    risk_decision_id: RiskDecisionId
    pair: CurrencyPair
    side: Side
    quantity: Decimal
    idempotency_key: str
    created_at: datetime


def _counts(path: Path) -> dict:
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


def _total(counts: dict) -> int:
    return sum(counts.values())


def _service(path: Path, clock, worker_identity: str = "worker-1") -> PaperApplicationService:
    store = SQLitePaperStore(path)
    return PaperApplicationService(store=store, clock=clock, worker_identity=worker_identity)


# ---------------------------------------------------------------------------
# Exact-type rejection: entry point
# ---------------------------------------------------------------------------


def test_entry_intent_rejects_close_intent_before_any_work(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    clock = _FixedClock(NOW)
    service = _service(path, clock)
    before = _counts(path)
    close_intent = synthetic_close_intent(
        position_id=PositionId("position-x"), quantity=Decimal("1")
    )

    with pytest.raises(TypeError):
        service.submit_entry_intent(
            close_intent,  # type: ignore[arg-type]
            authority=ExecutionAuthorityMode.PAPER,
            fill_policy=fill_policy(),
            account_bootstrap=bootstrap(),
        )

    assert clock.calls == 0
    assert _counts(path) == before


def test_entry_intent_rejects_liquidation_intent_before_any_work(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    clock = _FixedClock(NOW)
    service = _service(path, clock)
    before = _counts(path)

    with pytest.raises(TypeError):
        service.submit_entry_intent(
            liquidation_intent(),  # type: ignore[arg-type]
            authority=ExecutionAuthorityMode.PAPER,
            fill_policy=fill_policy(),
            account_bootstrap=bootstrap(),
        )

    assert clock.calls == 0
    assert _counts(path) == before


@pytest.mark.parametrize(
    "make_intent",
    [
        lambda base: Mock(),
        lambda base: _ExecutionIntentSubclass(
            base.intent_id,
            base.candidate_id,
            base.risk_decision_id,
            base.pair,
            base.side,
            base.quantity,
            base.idempotency_key,
            base.created_at,
        ),
        lambda base: _DuckExecutionIntent(
            base.intent_id,
            base.candidate_id,
            base.risk_decision_id,
            base.pair,
            base.side,
            base.quantity,
            base.idempotency_key,
            base.created_at,
        ),
    ],
    ids=["mock", "subclass", "duck-type"],
)
def test_entry_intent_rejects_non_exact_type_before_any_work(tmp_path: Path, make_intent) -> None:
    path = tmp_path / "live.sqlite"
    clock = _FixedClock(NOW)
    service = _service(path, clock)
    before = _counts(path)
    fake = make_intent(execution_intent())

    with pytest.raises(TypeError):
        service.submit_entry_intent(
            fake,
            authority=ExecutionAuthorityMode.PAPER,
            fill_policy=fill_policy(),
            account_bootstrap=bootstrap(),
        )

    assert clock.calls == 0
    assert _counts(path) == before


# ---------------------------------------------------------------------------
# Exact-type rejection: the two reduce-only entry points
# ---------------------------------------------------------------------------


def test_ordinary_close_entry_point_rejects_execution_intent(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    clock = _FixedClock(NOW)
    service = _service(path, clock)
    before = _counts(path)

    with pytest.raises(TypeError):
        service.submit_ordinary_close_intent(
            execution_intent(),  # type: ignore[arg-type]
            authority=ExecutionAuthorityMode.PAPER,
            fill_policy=fill_policy(),
            account_bootstrap=bootstrap(),
        )

    assert clock.calls == 0
    assert _counts(path) == before


def test_emergency_liquidation_entry_point_rejects_execution_intent(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    clock = _FixedClock(NOW)
    service = _service(path, clock)
    before = _counts(path)

    with pytest.raises(TypeError):
        service.submit_emergency_liquidation_intent(
            execution_intent(),  # type: ignore[arg-type]
            authority=ExecutionAuthorityMode.PAPER,
            existing_position_side=Side.BUY,
            fill_policy=fill_policy(),
            account_bootstrap=bootstrap(),
        )

    assert clock.calls == 0
    assert _counts(path) == before


# ---------------------------------------------------------------------------
# Authority routing
# ---------------------------------------------------------------------------


def test_live_authority_raises_before_clock_or_store_for_all_three_entry_points(
    tmp_path: Path,
) -> None:
    path = tmp_path / "live.sqlite"
    clock = _FixedClock(NOW)
    service = _service(path, clock)
    before = _counts(path)

    with pytest.raises(ValueError):
        service.submit_entry_intent(
            execution_intent(),
            authority=ExecutionAuthorityMode.LIVE,
            fill_policy=fill_policy(),
            account_bootstrap=bootstrap(),
        )
    with pytest.raises(ValueError):
        service.submit_emergency_liquidation_intent(
            liquidation_intent(),
            authority=ExecutionAuthorityMode.LIVE,
            existing_position_side=Side.BUY,
            fill_policy=fill_policy(),
            account_bootstrap=bootstrap(),
        )
    close_intent = synthetic_close_intent(
        position_id=PositionId("position-x"),
        quantity=Decimal("1"),
        authority=ExecutionAuthorityMode.PAPER,
    )
    with pytest.raises(ValueError):
        service.submit_ordinary_close_intent(
            close_intent,
            authority=ExecutionAuthorityMode.LIVE,
            fill_policy=fill_policy(),
            account_bootstrap=bootstrap(),
        )

    assert clock.calls == 0
    assert _counts(path) == before


def test_shadow_authority_returns_typed_result_and_writes_nothing(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    clock = _FixedClock(NOW)
    service = _service(path, clock)
    before = _counts(path)

    result = service.submit_entry_intent(
        execution_intent(),
        authority=ExecutionAuthorityMode.SHADOW_NOT_SUBMITTED,
        fill_policy=fill_policy(),
        account_bootstrap=bootstrap(),
    )

    assert result == PaperApplicationResult.shadow_not_submitted()
    assert result.disposition is PaperApplicationDisposition.SHADOW_NOT_SUBMITTED
    assert result.projected_order_state is None
    assert result.step_ordinal is None
    assert result.paper_fill_id is None
    assert result.reservation_consumption_id is None
    assert result.reservation_release_id is None
    assert clock.calls == 0
    assert _counts(path) == before
    assert _total(_counts(path)) == 0


def test_shadow_authority_for_reduce_only_entry_points_writes_nothing(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    clock = _FixedClock(NOW)
    service = _service(path, clock)

    liq_result = service.submit_emergency_liquidation_intent(
        liquidation_intent(),
        authority=ExecutionAuthorityMode.SHADOW_NOT_SUBMITTED,
        existing_position_side=Side.BUY,
        fill_policy=fill_policy(),
        account_bootstrap=bootstrap(),
    )
    close_intent = synthetic_close_intent(
        position_id=PositionId("position-x"),
        quantity=Decimal("1"),
        authority=ExecutionAuthorityMode.SHADOW_NOT_SUBMITTED,
    )
    close_result = service.submit_ordinary_close_intent(
        close_intent,
        authority=ExecutionAuthorityMode.SHADOW_NOT_SUBMITTED,
        fill_policy=fill_policy(),
        account_bootstrap=bootstrap(),
    )

    assert liq_result == PaperApplicationResult.shadow_not_submitted()
    assert close_result == PaperApplicationResult.shadow_not_submitted()
    assert _total(_counts(path)) == 0


def test_ordinary_close_intent_authority_mismatch_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    clock = _FixedClock(NOW)
    service = _service(path, clock)
    before = _counts(path)
    close_intent = synthetic_close_intent(
        position_id=PositionId("position-x"),
        quantity=Decimal("1"),
        authority=ExecutionAuthorityMode.PAPER,
    )

    with pytest.raises(ValueError):
        service.submit_ordinary_close_intent(
            close_intent,
            authority=ExecutionAuthorityMode.SHADOW_NOT_SUBMITTED,
            fill_policy=fill_policy(),
            account_bootstrap=bootstrap(),
        )

    assert clock.calls == 0
    assert _counts(path) == before


def test_authority_not_exact_execution_authority_mode_raises(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    clock = _FixedClock(NOW)
    service = _service(path, clock)

    with pytest.raises(TypeError):
        service.submit_entry_intent(
            execution_intent(),
            authority="PAPER",  # type: ignore[arg-type]
            fill_policy=fill_policy(),
            account_bootstrap=bootstrap(),
        )


# ---------------------------------------------------------------------------
# Clock: read exactly once, audit instant on every written row
# ---------------------------------------------------------------------------


def test_clock_is_read_exactly_once_and_every_row_carries_that_instant(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    clock = _FixedClock(NOW)
    service = _service(path, clock)
    mark = observation()

    result = service.submit_entry_intent(
        execution_intent(),
        authority=ExecutionAuthorityMode.PAPER,
        fill_policy=fill_policy(maximum_steps=1),
        account_bootstrap=bootstrap(),
        market_observations=(mark,),
    )

    assert clock.calls == 1
    assert result.disposition is PaperApplicationDisposition.PAPER_STEP_RESOLVED
    with sqlite3.connect(path) as connection:
        order_row = connection.execute(
            "SELECT created_at FROM live_paper_orders"
        ).fetchone()
        event_rows = connection.execute(
            "SELECT appended_at FROM live_paper_order_events"
        ).fetchall()
        step_row = connection.execute(
            "SELECT created_at FROM live_paper_fill_evaluation_steps"
        ).fetchone()
        fill_row = connection.execute("SELECT created_at FROM live_paper_fills").fetchone()
    expected = NOW.isoformat()
    assert order_row[0] == expected
    assert all(row[0] == expected for row in event_rows)
    assert step_row[0] == expected
    assert fill_row[0] == expected


# ---------------------------------------------------------------------------
# Clock failure-closed cases
# ---------------------------------------------------------------------------


class _NaiveDatetimeSubclass(datetime):
    pass


def test_clock_returning_non_datetime_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    clock = _FixedClock("2026-01-01T00:00:00+00:00")
    service = _service(path, clock)
    before = _counts(path)

    with pytest.raises(TypeError):
        service.submit_entry_intent(
            execution_intent(),
            authority=ExecutionAuthorityMode.PAPER,
            fill_policy=fill_policy(),
            account_bootstrap=bootstrap(),
        )

    assert clock.calls == 1
    assert _counts(path) == before


def test_clock_returning_datetime_subclass_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    clock = _FixedClock(_NaiveDatetimeSubclass(2026, 1, 1, tzinfo=UTC))
    service = _service(path, clock)
    before = _counts(path)

    with pytest.raises(TypeError):
        service.submit_entry_intent(
            execution_intent(),
            authority=ExecutionAuthorityMode.PAPER,
            fill_policy=fill_policy(),
            account_bootstrap=bootstrap(),
        )

    assert clock.calls == 1
    assert _counts(path) == before


def test_clock_returning_naive_datetime_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    clock = _FixedClock(datetime(2026, 1, 1))
    service = _service(path, clock)
    before = _counts(path)

    with pytest.raises(ValueError):
        service.submit_entry_intent(
            execution_intent(),
            authority=ExecutionAuthorityMode.PAPER,
            fill_policy=fill_policy(),
            account_bootstrap=bootstrap(),
        )

    assert clock.calls == 1
    assert _counts(path) == before


def test_clock_returning_non_utc_offset_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    jst = datetime(2026, 1, 1, 9, 0, 0, tzinfo=timezone(timedelta(hours=9)))
    clock = _FixedClock(jst)
    service = _service(path, clock)
    before = _counts(path)

    with pytest.raises(ValueError):
        service.submit_entry_intent(
            execution_intent(),
            authority=ExecutionAuthorityMode.PAPER,
            fill_policy=fill_policy(),
            account_bootstrap=bootstrap(),
        )

    assert clock.calls == 1
    assert _counts(path) == before


# ---------------------------------------------------------------------------
# Manufactured-terminal regression guard: NO_MARKET is only ever reached by a
# Clock that genuinely crossed the Step's own due boundary, never a caller
# argument (there is none on the entry-point signature).
# ---------------------------------------------------------------------------


def test_clock_before_due_leaves_step_pending(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    no_obs_pair = CurrencyPair.parse("EUR_JPY")
    policy = fill_policy(maximum_steps=1, step_window_duration=timedelta(minutes=1))
    clock = _FixedClock(NOW)
    service = _service(path, clock)

    result = service.submit_entry_intent(
        execution_intent(pair=no_obs_pair, idempotency_key="entry-no-market-pending"),
        authority=ExecutionAuthorityMode.PAPER,
        fill_policy=policy,
        account_bootstrap=bootstrap(),
    )

    assert result.disposition is PaperApplicationDisposition.PAPER_STEP_PENDING
    assert result.projected_order_state is PaperOrderState.OPEN
    assert result.paper_fill_id is None


def test_clock_past_due_resolves_no_market_terminal(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    no_obs_pair = CurrencyPair.parse("EUR_JPY")
    policy = fill_policy(maximum_steps=1, step_window_duration=timedelta(minutes=1))
    due = NOW + timedelta(minutes=1)
    clock = _FixedClock(due + timedelta(seconds=1))
    service = _service(path, clock)

    result = service.submit_entry_intent(
        execution_intent(pair=no_obs_pair, idempotency_key="entry-no-market-terminal"),
        authority=ExecutionAuthorityMode.PAPER,
        fill_policy=policy,
        account_bootstrap=bootstrap(),
    )

    assert result.disposition is PaperApplicationDisposition.PAPER_STEP_RESOLVED
    assert result.projected_order_state is policy.no_fill_terminal_order_state
    assert result.paper_fill_id is None
    assert result.reservation_consumption_id is None
    assert result.reservation_release_id is None


# ---------------------------------------------------------------------------
# Single call, no loop, no retry: each B4 transaction is invoked exactly once.
# ---------------------------------------------------------------------------


def test_single_call_invokes_each_store_transaction_exactly_once(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    clock = _FixedClock(NOW)
    store = SQLitePaperStore(path)
    service = PaperApplicationService(store=store, clock=clock, worker_identity="w1")
    mark = observation()

    with (
        patch.object(store, "accept_entry_order", wraps=store.accept_entry_order) as accept_spy,
        patch.object(store, "create_step", wraps=store.create_step) as create_spy,
        patch.object(
            store, "append_market_observations", wraps=store.append_market_observations
        ) as append_spy,
        patch.object(store, "evaluate_step", wraps=store.evaluate_step) as evaluate_spy,
    ):
        result = service.submit_entry_intent(
            execution_intent(),
            authority=ExecutionAuthorityMode.PAPER,
            fill_policy=fill_policy(maximum_steps=1),
            account_bootstrap=bootstrap(),
            market_observations=(mark,),
        )

    assert result.disposition is PaperApplicationDisposition.PAPER_STEP_RESOLVED
    assert accept_spy.call_count == 1
    assert create_spy.call_count == 1
    assert append_spy.call_count == 1
    assert evaluate_spy.call_count == 1
    assert clock.calls == 1


# ---------------------------------------------------------------------------
# Manual exact replay is idempotent: same Clock value twice adds zero rows.
# ---------------------------------------------------------------------------


def test_manual_exact_replay_adds_no_semantic_rows_and_returns_same_result(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    clock = _FixedClock(NOW)
    service = _service(path, clock)
    mark = observation()
    intent = execution_intent()

    first = service.submit_entry_intent(
        intent,
        authority=ExecutionAuthorityMode.PAPER,
        fill_policy=fill_policy(maximum_steps=1),
        account_bootstrap=bootstrap(),
        market_observations=(mark,),
    )
    before = _counts(path)

    second = service.submit_entry_intent(
        intent,
        authority=ExecutionAuthorityMode.PAPER,
        fill_policy=fill_policy(maximum_steps=1),
        account_bootstrap=bootstrap(),
        market_observations=(mark,),
    )
    after = _counts(path)

    assert first == second
    assert before == after


# ---------------------------------------------------------------------------
# Multi-step continuation: the fixed P0 defect (_advance must target the plan's
# actual current Step ordinal, not always ordinal 0).
# ---------------------------------------------------------------------------


def test_single_step_policy_stays_at_ordinal_zero_even_with_a_remainder(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    policy = fill_policy(
        maximum_steps=1,
        partial_fill_mode=PaperPartialFillMode.FRACTION_OF_REMAINING,
        partial_fill_fraction=Decimal("0.4"),
    )
    clock = _SequenceClock([NOW, NOW])
    service = _service(path, clock)
    mark = observation()
    intent = execution_intent(idempotency_key="entry-single-step-remainder")

    first = service.submit_entry_intent(
        intent,
        authority=ExecutionAuthorityMode.PAPER,
        fill_policy=policy,
        account_bootstrap=bootstrap(),
        market_observations=(mark,),
    )
    assert first.step_ordinal == 0
    assert first.projected_order_state is policy.incomplete_terminal_order_state

    second = service.submit_entry_intent(
        intent,
        authority=ExecutionAuthorityMode.PAPER,
        fill_policy=policy,
        account_bootstrap=bootstrap(),
        market_observations=(mark,),
    )

    assert second == first


## The three entry points read the Clock and call T1 (order acceptance) only for a
## genuinely new intent; a repeat call for an already-accepted intent skips T1 and
## resolves the persisted AcceptedOrder via `hydrate_accepted_order` instead (see
## `PaperApplicationService._accept_or_reuse`), so every test below drives the
## *public* entry point across multiple calls with a `Clock` double advancing to
## each call's own window/due instant -- precisely the "later call for the same
## intent" scenario the fixed defect describes.


def test_repeat_call_resolves_a_step_left_pending_by_call_one(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    no_obs_pair = CurrencyPair.parse("EUR_JPY")
    policy = fill_policy(maximum_steps=1, step_window_duration=timedelta(minutes=1))
    intent = execution_intent(pair=no_obs_pair, idempotency_key="entry-resume-pending")
    due0 = intent.created_at + policy.step_window_duration
    later = due0 + timedelta(seconds=1)

    clock = _SequenceClock([intent.created_at, later])
    service = _service(path, clock)

    first = service.submit_entry_intent(
        intent,
        authority=ExecutionAuthorityMode.PAPER,
        fill_policy=policy,
        account_bootstrap=bootstrap(),
    )
    assert first.disposition is PaperApplicationDisposition.PAPER_STEP_PENDING
    assert first.step_ordinal == 0

    second = service.submit_entry_intent(
        intent,
        authority=ExecutionAuthorityMode.PAPER,
        fill_policy=policy,
        account_bootstrap=bootstrap(),
    )

    assert second.disposition is PaperApplicationDisposition.PAPER_STEP_RESOLVED
    assert second.step_ordinal == 0
    assert second.projected_order_state is policy.no_fill_terminal_order_state

    with sqlite3.connect(path) as connection:
        step_count = connection.execute(
            "SELECT COUNT(*) FROM live_paper_fill_evaluation_steps"
        ).fetchone()[0]
    assert step_count == 1


def test_multi_step_continuation_creates_and_evaluates_step_one_not_step_zero(
    tmp_path: Path,
) -> None:
    path = tmp_path / "live.sqlite"
    policy = fill_policy(
        maximum_steps=3,
        partial_fill_mode=PaperPartialFillMode.FRACTION_OF_REMAINING,
        partial_fill_fraction=Decimal("0.4"),
    )
    account = bootstrap()
    intent = execution_intent(quantity=Decimal("1000"), idempotency_key="entry-continuation")
    window_start0 = intent.created_at
    due0 = window_start0 + policy.step_window_duration
    window_start1 = due0 + policy.step_gap

    clock = _SequenceClock([window_start0, window_start1])
    service = _service(path, clock)
    mark0 = observation(received_at=window_start0, provider_observed_at=window_start0)

    first = service.submit_entry_intent(
        intent,
        authority=ExecutionAuthorityMode.PAPER,
        fill_policy=policy,
        account_bootstrap=account,
        market_observations=(mark0,),
    )
    assert first.disposition is PaperApplicationDisposition.PAPER_STEP_RESOLVED
    assert first.step_ordinal == 0
    assert first.projected_order_state is PaperOrderState.PARTIALLY_FILLED

    with sqlite3.connect(path) as connection:
        step0_before = connection.execute(
            "SELECT * FROM live_paper_fill_evaluation_steps WHERE ordinal = 0"
        ).fetchone()

    mark1 = observation(received_at=window_start1, provider_observed_at=window_start1)
    second = service.submit_entry_intent(
        intent,
        authority=ExecutionAuthorityMode.PAPER,
        fill_policy=policy,
        account_bootstrap=account,
        market_observations=(mark1,),
    )

    assert second.disposition is PaperApplicationDisposition.PAPER_STEP_RESOLVED
    assert second.step_ordinal == 1
    assert second.projected_order_state is PaperOrderState.PARTIALLY_FILLED

    with sqlite3.connect(path) as connection:
        step0_after = connection.execute(
            "SELECT * FROM live_paper_fill_evaluation_steps WHERE ordinal = 0"
        ).fetchone()
        step1_row = connection.execute(
            "SELECT * FROM live_paper_fill_evaluation_steps WHERE ordinal = 1"
        ).fetchone()
        order_count = connection.execute(
            "SELECT COUNT(*) FROM live_paper_orders"
        ).fetchone()[0]

    assert step0_before == step0_after
    assert step1_row is not None
    assert order_count == 1


def test_multi_step_pending_continuation_projects_partially_filled(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    policy = fill_policy(
        maximum_steps=3,
        partial_fill_mode=PaperPartialFillMode.FRACTION_OF_REMAINING,
        partial_fill_fraction=Decimal("0.4"),
    )
    account = bootstrap()
    intent = execution_intent(quantity=Decimal("1000"), idempotency_key="entry-pending-partial")
    window_start0 = intent.created_at
    due0 = window_start0 + policy.step_window_duration
    window_start1 = due0 + policy.step_gap

    clock = _SequenceClock([window_start0, window_start1])
    service = _service(path, clock)
    mark0 = observation(received_at=window_start0, provider_observed_at=window_start0)
    service.submit_entry_intent(
        intent,
        authority=ExecutionAuthorityMode.PAPER,
        fill_policy=policy,
        account_bootstrap=account,
        market_observations=(mark0,),
    )

    second = service.submit_entry_intent(
        intent,
        authority=ExecutionAuthorityMode.PAPER,
        fill_policy=policy,
        account_bootstrap=account,
    )

    assert second.disposition is PaperApplicationDisposition.PAPER_STEP_PENDING
    assert second.step_ordinal == 1
    assert second.projected_order_state is PaperOrderState.PARTIALLY_FILLED


def test_full_multi_step_completion_then_terminal_replay_creates_no_new_step(
    tmp_path: Path,
) -> None:
    path = tmp_path / "live.sqlite"
    policy = fill_policy(
        maximum_steps=3,
        partial_fill_mode=PaperPartialFillMode.FRACTION_OF_REMAINING,
        partial_fill_fraction=Decimal("0.4"),
    )
    account = bootstrap()
    intent = execution_intent(quantity=Decimal("1000"), idempotency_key="entry-full-completion")
    window_start0 = intent.created_at
    due0 = window_start0 + policy.step_window_duration
    window_start1 = due0 + policy.step_gap
    due1 = window_start1 + policy.step_window_duration
    window_start2 = due1 + policy.step_gap
    replay_at = window_start2 + timedelta(hours=1)

    clock = _SequenceClock([window_start0, window_start1, window_start2, replay_at])
    service = _service(path, clock)
    mark0 = observation(received_at=window_start0, provider_observed_at=window_start0)
    mark1 = observation(received_at=window_start1, provider_observed_at=window_start1)
    mark2 = observation(received_at=window_start2, provider_observed_at=window_start2)

    service.submit_entry_intent(
        intent,
        authority=ExecutionAuthorityMode.PAPER,
        fill_policy=policy,
        account_bootstrap=account,
        market_observations=(mark0,),
    )
    service.submit_entry_intent(
        intent,
        authority=ExecutionAuthorityMode.PAPER,
        fill_policy=policy,
        account_bootstrap=account,
        market_observations=(mark1,),
    )
    third = service.submit_entry_intent(
        intent,
        authority=ExecutionAuthorityMode.PAPER,
        fill_policy=policy,
        account_bootstrap=account,
        market_observations=(mark2,),
    )

    assert third.disposition is PaperApplicationDisposition.PAPER_STEP_RESOLVED
    assert third.step_ordinal == 2
    assert third.projected_order_state is policy.incomplete_terminal_order_state

    with sqlite3.connect(path) as connection:
        step_count_before = connection.execute(
            "SELECT COUNT(*) FROM live_paper_fill_evaluation_steps"
        ).fetchone()[0]

    # A later Clock reading on a genuinely terminal order (no legitimate
    # continuation) still replays the terminal result via T1-skip + T2's own
    # current_step_ordinal/terminal-claim replay, never a new Step.
    fourth = service.submit_entry_intent(
        intent,
        authority=ExecutionAuthorityMode.PAPER,
        fill_policy=policy,
        account_bootstrap=account,
        market_observations=(mark2,),
    )

    with sqlite3.connect(path) as connection:
        step_count_after = connection.execute(
            "SELECT COUNT(*) FROM live_paper_fill_evaluation_steps"
        ).fetchone()[0]
        order_count = connection.execute(
            "SELECT COUNT(*) FROM live_paper_orders"
        ).fetchone()[0]

    assert fourth == third
    assert step_count_after == step_count_before
    assert order_count == 1


# ---------------------------------------------------------------------------
# The identity-mismatch fail-closed case: a second call for the same intent
# with a different fill_policy fails closed instead of silently proceeding.
# ---------------------------------------------------------------------------


def test_repeat_call_with_different_fill_policy_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    clock = _SequenceClock([NOW, NOW])
    service = _service(path, clock)
    intent = execution_intent(idempotency_key="entry-identity-mismatch")
    mark = observation()

    first = service.submit_entry_intent(
        intent,
        authority=ExecutionAuthorityMode.PAPER,
        fill_policy=fill_policy(maximum_steps=1),
        account_bootstrap=bootstrap(),
        market_observations=(mark,),
    )
    assert first.disposition is PaperApplicationDisposition.PAPER_STEP_RESOLVED

    with pytest.raises(PaperPersistenceConflict):
        service.submit_entry_intent(
            intent,
            authority=ExecutionAuthorityMode.PAPER,
            fill_policy=fill_policy(maximum_steps=2),
            account_bootstrap=bootstrap(),
        )


def test_filled_order_replay_returns_same_result_and_creates_no_step(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    policy = fill_policy(maximum_steps=3, partial_fill_mode=PaperPartialFillMode.FULL_REMAINING)
    clock = _SequenceClock([NOW, NOW])
    service = _service(path, clock)
    mark = observation()
    intent = execution_intent(idempotency_key="entry-filled-replay")

    first = service.submit_entry_intent(
        intent,
        authority=ExecutionAuthorityMode.PAPER,
        fill_policy=policy,
        account_bootstrap=bootstrap(),
        market_observations=(mark,),
    )
    assert first.disposition is PaperApplicationDisposition.PAPER_STEP_RESOLVED
    assert first.projected_order_state is PaperOrderState.FILLED
    assert first.step_ordinal == 0

    with sqlite3.connect(path) as connection:
        step_count_before = connection.execute(
            "SELECT COUNT(*) FROM live_paper_fill_evaluation_steps"
        ).fetchone()[0]

    second = service.submit_entry_intent(
        intent,
        authority=ExecutionAuthorityMode.PAPER,
        fill_policy=policy,
        account_bootstrap=bootstrap(),
        market_observations=(mark,),
    )

    with sqlite3.connect(path) as connection:
        step_count_after = connection.execute(
            "SELECT COUNT(*) FROM live_paper_fill_evaluation_steps"
        ).fetchone()[0]

    assert second == first
    assert step_count_after == step_count_before


def test_no_fill_terminal_replay_returns_same_result_and_creates_no_step(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    no_obs_pair = CurrencyPair.parse("EUR_JPY")
    policy = fill_policy(maximum_steps=3, step_window_duration=timedelta(minutes=1))
    due = NOW + timedelta(minutes=1)
    later = due + timedelta(seconds=1)
    clock = _SequenceClock([later, later])
    service = _service(path, clock)
    intent = execution_intent(pair=no_obs_pair, idempotency_key="entry-no-market-replay")

    first = service.submit_entry_intent(
        intent,
        authority=ExecutionAuthorityMode.PAPER,
        fill_policy=policy,
        account_bootstrap=bootstrap(),
    )
    assert first.disposition is PaperApplicationDisposition.PAPER_STEP_RESOLVED
    assert first.projected_order_state is policy.no_fill_terminal_order_state
    assert first.step_ordinal == 0

    with sqlite3.connect(path) as connection:
        step_count_before = connection.execute(
            "SELECT COUNT(*) FROM live_paper_fill_evaluation_steps"
        ).fetchone()[0]

    second = service.submit_entry_intent(
        intent,
        authority=ExecutionAuthorityMode.PAPER,
        fill_policy=policy,
        account_bootstrap=bootstrap(),
    )

    with sqlite3.connect(path) as connection:
        step_count_after = connection.execute(
            "SELECT COUNT(*) FROM live_paper_fill_evaluation_steps"
        ).fetchone()[0]

    assert second == first
    assert step_count_after == step_count_before


# ---------------------------------------------------------------------------
# Runtime zero-Broker probe.
# ---------------------------------------------------------------------------


def test_runtime_probe_never_touches_broker_or_execution_symbols(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    clock = _SequenceClock([NOW, NOW, NOW])
    service = _service(path, clock)
    mark = observation()

    with (
        patch.object(GmoPrivatePostTransport, "__init__", return_value=None) as init_mock,
        patch.object(GmoPrivatePostTransport, "post_once") as post_mock,
        patch.object(ExecutionService, "submit") as submit_mock,
        patch.object(LiveArmPolicy, "is_armed") as armed_mock,
    ):
        entry_result = service.submit_entry_intent(
            execution_intent(idempotency_key="probe-entry"),
            authority=ExecutionAuthorityMode.PAPER,
            fill_policy=fill_policy(maximum_steps=1),
            account_bootstrap=bootstrap(),
            market_observations=(mark,),
        )
        assert entry_result.disposition is PaperApplicationDisposition.PAPER_STEP_RESOLVED

        position_id = None
        with sqlite3.connect(path) as connection:
            row = connection.execute(
                "SELECT paper_position_id FROM live_paper_positions"
            ).fetchone()
            position_id = row[0]

        close_intent = synthetic_close_intent(
            position_id=PositionId(position_id),
            side=Side.SELL,
            quantity=Decimal("500"),
        )
        insert_m2d_intent(path, close_intent)
        close_result = service.submit_ordinary_close_intent(
            close_intent,
            authority=ExecutionAuthorityMode.PAPER,
            fill_policy=fill_policy(
                policy_version="close-policy-v1", maximum_steps=1
            ),
            account_bootstrap=bootstrap(),
            market_observations=(mark,),
        )
        assert close_result.disposition is PaperApplicationDisposition.PAPER_STEP_RESOLVED

        liq_result = service.submit_emergency_liquidation_intent(
            liquidation_intent(position_id=PositionId(position_id), quantity=Decimal("500")),
            authority=ExecutionAuthorityMode.PAPER,
            existing_position_side=Side.BUY,
            fill_policy=fill_policy(
                policy_version="liq-policy-v1", maximum_steps=1
            ),
            account_bootstrap=bootstrap(),
            market_observations=(mark,),
        )
        assert liq_result.disposition is PaperApplicationDisposition.PAPER_STEP_RESOLVED

        assert init_mock.call_count == 0
        assert post_mock.call_count == 0
        assert submit_mock.call_count == 0
        assert armed_mock.call_count == 0
