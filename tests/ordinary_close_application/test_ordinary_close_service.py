import dataclasses
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from swap_bot.execution_authority import ExecutionAuthorityMode
from swap_bot.ordinary_close_application import (
    OrdinaryCloseApplicationResult,
    OrdinaryCloseApplicationService,
)
from swap_bot.ordinary_close_store import (
    OrdinaryClosePersistenceDisposition,
    OrdinaryClosePersistenceResult,
    OrdinaryCloseReservationDisposition,
    OrdinaryCloseReservationPersistenceResult,
    SQLiteOrdinaryCloseStore,
)
from swap_bot.strategy import (
    NewsFilteredCarryStrategyConfig,
    OrdinaryCloseAllocationPolicy,
    OrdinaryClosePortfolioDisposition,
    OrdinaryCloseReservationEntry,
    OrdinaryCloseReservationSnapshot,
    OrdinaryCloseRiskOutcome,
    OrdinaryPositionExitEvaluator,
    OrdinaryPositionExitWorkItem,
    PositionCloseCapacityEvidence,
    PositionExitEvaluationOutcome,
    evaluate_ordinary_close_portfolio_and_risk,
)

from tests.strategy_contracts.factories import strategy_config
from tests.strategy_persistence.test_ordinary_close_store import (
    _counts,
    _fixture,
    _reservation_counts,
)

# ---------------------------------------------------------------------------
# In-memory fake store: exercises real B1/B2 typed evaluation/decision logic
# without a database, so composition (call ordering, KEEP short-circuit, LIVE
# prevalidation) can be asserted independently of B3/B4 persistence mechanics.
# ---------------------------------------------------------------------------


class _FakeStore:
    def __init__(self, *, prior_entries: tuple[OrdinaryCloseReservationEntry, ...] = ()) -> None:
        self.evaluate_calls: list[str] = []
        self.reservation_calls: list[str] = []
        self._prior_entries = prior_entries

    def evaluate_and_persist(
        self, work_item: OrdinaryPositionExitWorkItem, *, config: NewsFilteredCarryStrategyConfig
    ) -> OrdinaryClosePersistenceResult:
        self.evaluate_calls.append(work_item.work_item_id)
        operational_result = OrdinaryPositionExitEvaluator(config).evaluate(work_item)
        return OrdinaryClosePersistenceResult(
            OrdinaryClosePersistenceDisposition.INSERTED, operational_result
        )

    def evaluate_and_persist_reservation(
        self,
        evaluation_result,  # type: ignore[no-untyped-def]
        *,
        capacity: PositionCloseCapacityEvidence,
        allocation_policy: OrdinaryCloseAllocationPolicy,
        risk_policy,  # type: ignore[no-untyped-def]
        authority: ExecutionAuthorityMode,
    ) -> OrdinaryCloseReservationPersistenceResult:
        self.reservation_calls.append(evaluation_result.operational_evaluation_id)
        snapshot = OrdinaryCloseReservationSnapshot(capacity.position_id, self._prior_entries)
        portfolio_decision, risk_decision, intent = evaluate_ordinary_close_portfolio_and_risk(
            evaluation_result,
            capacity=capacity,
            reservation_snapshot=snapshot,
            allocation_policy=allocation_policy,
            risk_policy=risk_policy,
            authority=authority,
        )
        return OrdinaryCloseReservationPersistenceResult(
            OrdinaryCloseReservationDisposition.INSERTED, portfolio_decision, risk_decision, intent
        )


# ---------------------------------------------------------------------------
# Composition behavior against the in-memory fake store
# ---------------------------------------------------------------------------


def test_keep_result_carries_no_reservation_and_never_calls_reservation_store(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path, score=0.0)
    store = _FakeStore()
    service = OrdinaryCloseApplicationService(persistence_store=store)  # type: ignore[arg-type]

    result = service.run(fixture.work_item, config=strategy_config())

    assert result.outcome is PositionExitEvaluationOutcome.KEEP
    assert result.reservation is None
    assert result.approved_intent is None
    assert store.evaluate_calls == [fixture.work_item.work_item_id]
    assert store.reservation_calls == []


def test_close_accept_approve_yields_exactly_one_intent(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, target_fraction=Decimal("1"))
    store = _FakeStore()
    service = OrdinaryCloseApplicationService(persistence_store=store)  # type: ignore[arg-type]

    result = service.run(fixture.work_item, config=strategy_config())

    assert result.outcome is PositionExitEvaluationOutcome.CLOSE_CANDIDATE
    assert result.reservation is not None
    assert result.reservation.portfolio_decision.disposition is (
        OrdinaryClosePortfolioDisposition.ACCEPT
    )
    assert result.reservation.risk_decision.outcome is OrdinaryCloseRiskOutcome.APPROVE
    assert result.approved_intent is not None
    assert result.approved_intent.quantity == Decimal("1000")
    assert store.reservation_calls == [
        result.evaluation_persistence.result.operational_evaluation_id
    ]


def test_close_portfolio_reject_yields_linked_risk_reject_and_no_intent(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path, target_fraction=Decimal("1"))
    prior = (OrdinaryCloseReservationEntry("prior-intent-1", Decimal("1000")),)
    store = _FakeStore(prior_entries=prior)
    service = OrdinaryCloseApplicationService(persistence_store=store)  # type: ignore[arg-type]

    result = service.run(fixture.work_item, config=strategy_config())

    assert result.reservation is not None
    assert result.reservation.portfolio_decision.disposition is (
        OrdinaryClosePortfolioDisposition.REJECT
    )
    assert result.reservation.risk_decision.outcome is OrdinaryCloseRiskOutcome.REJECT
    assert result.reservation.intent is None
    assert result.approved_intent is None


def test_live_authority_stops_before_evaluate_or_reservation_calls(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, authority=ExecutionAuthorityMode.LIVE)
    store = _FakeStore()
    service = OrdinaryCloseApplicationService(persistence_store=store)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="LIVE authority is rejected"):
        service.run(fixture.work_item, config=strategy_config())

    assert store.evaluate_calls == []
    assert store.reservation_calls == []


# ---------------------------------------------------------------------------
# Result-type invariants: invalid KEEP/CLOSE combinations fail closed
# ---------------------------------------------------------------------------


def test_result_rejects_keep_outcome_carrying_reservation_data(tmp_path: Path) -> None:
    keep_dir = tmp_path / "keep"
    close_dir = tmp_path / "close"
    keep_dir.mkdir()
    close_dir.mkdir()
    keep_fixture = _fixture(keep_dir, score=0.0)
    close_fixture = _fixture(close_dir, target_fraction=Decimal("1"))
    store = _FakeStore()
    keep_result = OrdinaryCloseApplicationService(
        persistence_store=store  # type: ignore[arg-type]
    ).run(keep_fixture.work_item, config=strategy_config())
    close_result = OrdinaryCloseApplicationService(
        persistence_store=store  # type: ignore[arg-type]
    ).run(close_fixture.work_item, config=strategy_config())

    with pytest.raises(ValueError, match="KEEP result cannot carry reservation data"):
        OrdinaryCloseApplicationResult(
            keep_fixture.work_item.work_item_id,
            PositionExitEvaluationOutcome.KEEP,
            keep_result.evaluation_persistence,
            close_result.reservation,
        )


def test_result_rejects_close_outcome_missing_reservation_data(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, target_fraction=Decimal("1"))
    store = _FakeStore()
    result = OrdinaryCloseApplicationService(
        persistence_store=store  # type: ignore[arg-type]
    ).run(fixture.work_item, config=strategy_config())

    with pytest.raises(TypeError, match="requires exact reservation persistence"):
        OrdinaryCloseApplicationResult(
            fixture.work_item.work_item_id,
            PositionExitEvaluationOutcome.CLOSE_CANDIDATE,
            result.evaluation_persistence,
            None,
        )


def test_result_rejects_close_outcome_carrying_reservation_from_another_candidate(
    tmp_path: Path,
) -> None:
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()
    first_fixture = _fixture(first_dir, target_fraction=Decimal("1"))
    second_fixture = _fixture(second_dir, target_fraction=Decimal("1"), score=-1.0)
    store = _FakeStore()
    first_result = OrdinaryCloseApplicationService(
        persistence_store=store  # type: ignore[arg-type]
    ).run(first_fixture.work_item, config=strategy_config())
    second_result = OrdinaryCloseApplicationService(
        persistence_store=store  # type: ignore[arg-type]
    ).run(second_fixture.work_item, config=strategy_config())

    assert first_result.reservation is not None
    assert second_result.reservation is not None

    with pytest.raises(ValueError, match="does not belong to this evaluation's Candidate"):
        OrdinaryCloseApplicationResult(
            first_fixture.work_item.work_item_id,
            PositionExitEvaluationOutcome.CLOSE_CANDIDATE,
            first_result.evaluation_persistence,
            second_result.reservation,
        )


# ---------------------------------------------------------------------------
# Real SQLiteOrdinaryCloseStore: end-to-end proof through B3 and B4
# ---------------------------------------------------------------------------


def _all_ordinary_close_counts(path: Path) -> tuple[int, ...]:
    return _counts(path) + _reservation_counts(path)


def test_real_store_keep_path_writes_zero_reservation_rows(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, score=0.0)
    service = OrdinaryCloseApplicationService(
        persistence_store=SQLiteOrdinaryCloseStore(fixture.live)
    )

    result = service.run(fixture.work_item, config=strategy_config())

    assert result.outcome is PositionExitEvaluationOutcome.KEEP
    assert _reservation_counts(fixture.live) == (0, 0, 0)


def test_real_store_close_accept_approve_persists_exactly_one_intent(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path, target_fraction=Decimal("1"))
    service = OrdinaryCloseApplicationService(
        persistence_store=SQLiteOrdinaryCloseStore(fixture.live)
    )

    result = service.run(fixture.work_item, config=strategy_config())

    assert result.approved_intent is not None
    assert _reservation_counts(fixture.live) == (1, 1, 1)


def test_real_store_live_authority_rejects_with_zero_rows_in_every_table(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path, authority=ExecutionAuthorityMode.LIVE)
    service = OrdinaryCloseApplicationService(
        persistence_store=SQLiteOrdinaryCloseStore(fixture.live)
    )

    with pytest.raises(ValueError, match="LIVE authority is rejected"):
        service.run(fixture.work_item, config=strategy_config())

    assert _all_ordinary_close_counts(fixture.live) == (0, 0, 0, 0, 0, 0, 0, 0)


def test_real_store_manual_exact_replay_converges_through_b3_and_b4(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path, target_fraction=Decimal("0.5"))
    service = OrdinaryCloseApplicationService(
        persistence_store=SQLiteOrdinaryCloseStore(fixture.live)
    )

    first = service.run(fixture.work_item, config=strategy_config())
    second = service.run(fixture.work_item, config=strategy_config())

    assert first.evaluation_persistence.disposition is (
        OrdinaryClosePersistenceDisposition.INSERTED
    )
    assert second.evaluation_persistence.disposition is (
        OrdinaryClosePersistenceDisposition.REUSED_IDENTICAL
    )
    assert second.reservation is not None and first.reservation is not None
    assert second.reservation.disposition is OrdinaryCloseReservationDisposition.REUSED_IDENTICAL
    assert second.reservation.portfolio_decision == first.reservation.portfolio_decision
    assert second.reservation.risk_decision == first.reservation.risk_decision
    assert second.reservation.intent == first.reservation.intent
    assert second.approved_intent == first.approved_intent
    assert _counts(fixture.live) == (1, 1, 1, 1, 1)
    assert _reservation_counts(fixture.live) == (1, 1, 1)


def test_real_store_second_close_request_for_same_position_is_portfolio_rejected(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path, target_fraction=Decimal("1"))
    store = SQLiteOrdinaryCloseStore(fixture.live)
    service = OrdinaryCloseApplicationService(persistence_store=store)
    first = service.run(fixture.work_item, config=strategy_config())
    assert first.approved_intent is not None

    later_capacity = PositionCloseCapacityEvidence.create(
        capacity_contract_version=fixture.work_item.capacity.capacity_contract_version,
        position_id=fixture.work_item.capacity.position_id,
        position_evidence_id=fixture.work_item.capacity.position_evidence_id,
        pair=fixture.work_item.capacity.pair,
        existing_position_side=fixture.work_item.capacity.existing_position_side,
        position_observed_at=fixture.work_item.capacity.position_observed_at,
        open_quantity=Decimal("1000"),
        quantity_unit="BASE_UNITS",
        source=fixture.work_item.capacity.source,
        checkpoint_id="position-checkpoint-2",
    )
    later_work_item = OrdinaryPositionExitWorkItem.create(
        evaluation_input=dataclasses.replace(
            fixture.work_item.evaluation_input,
            evaluated_at=fixture.work_item.evaluation_input.evaluated_at
            + timedelta(seconds=10),
        ),
        capacity=later_capacity,
        signal_resolution=fixture.work_item.signal_resolution,
        swap_resolution=fixture.work_item.swap_resolution,
        allocation_policy=fixture.work_item.allocation_policy,
        risk_policy=fixture.work_item.risk_policy,
        authority=fixture.work_item.authority,
    )

    second = service.run(later_work_item, config=strategy_config())

    assert second.reservation is not None
    assert second.reservation.portfolio_decision.disposition is (
        OrdinaryClosePortfolioDisposition.REJECT
    )
    assert second.reservation.risk_decision.outcome is OrdinaryCloseRiskOutcome.REJECT
    assert second.approved_intent is None
    assert _reservation_counts(fixture.live) == (2, 2, 1)


def test_real_store_second_close_request_for_same_position_is_portfolio_reduced(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path, target_fraction=Decimal("0.4"))
    store = SQLiteOrdinaryCloseStore(fixture.live)
    service = OrdinaryCloseApplicationService(persistence_store=store)
    first = service.run(fixture.work_item, config=strategy_config())
    assert first.approved_intent is not None
    assert first.approved_intent.quantity == Decimal("400")

    later_capacity = PositionCloseCapacityEvidence.create(
        capacity_contract_version=fixture.work_item.capacity.capacity_contract_version,
        position_id=fixture.work_item.capacity.position_id,
        position_evidence_id=fixture.work_item.capacity.position_evidence_id,
        pair=fixture.work_item.capacity.pair,
        existing_position_side=fixture.work_item.capacity.existing_position_side,
        position_observed_at=fixture.work_item.capacity.position_observed_at,
        open_quantity=Decimal("1000"),
        quantity_unit="BASE_UNITS",
        source=fixture.work_item.capacity.source,
        checkpoint_id="position-checkpoint-2",
    )
    later_work_item = OrdinaryPositionExitWorkItem.create(
        evaluation_input=dataclasses.replace(
            fixture.work_item.evaluation_input,
            evaluated_at=fixture.work_item.evaluation_input.evaluated_at
            + timedelta(seconds=10),
        ),
        capacity=later_capacity,
        signal_resolution=fixture.work_item.signal_resolution,
        swap_resolution=fixture.work_item.swap_resolution,
        allocation_policy=OrdinaryCloseAllocationPolicy("allocation-v1", Decimal("1")),
        risk_policy=fixture.work_item.risk_policy,
        authority=fixture.work_item.authority,
    )

    second = service.run(later_work_item, config=strategy_config())

    assert second.reservation is not None
    assert second.reservation.portfolio_decision.disposition is (
        OrdinaryClosePortfolioDisposition.REDUCE
    )
    assert second.reservation.portfolio_decision.available_before == Decimal("600")
    assert second.reservation.portfolio_decision.allocated_quantity == Decimal("600")
    assert second.reservation.risk_decision.outcome is OrdinaryCloseRiskOutcome.APPROVE
    assert second.approved_intent is not None
    assert second.approved_intent.quantity == Decimal("600")
