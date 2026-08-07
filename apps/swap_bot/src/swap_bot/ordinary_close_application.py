from __future__ import annotations

from dataclasses import dataclass

from .execution_authority import ExecutionAuthorityMode
from .ordinary_close_store import (
    OrdinaryClosePersistenceResult,
    OrdinaryCloseReservationPersistenceResult,
    SQLiteOrdinaryCloseStore,
)
from .strategy import (
    ApprovedCloseIntent,
    NewsFilteredCarryStrategyConfig,
    OrdinaryPositionExitWorkItem,
    PositionExitEvaluationOutcome,
)


@dataclass(frozen=True, slots=True)
class OrdinaryCloseApplicationResult:
    """One work item's terminal outcome: KEEP, or CLOSE with its Portfolio/Risk chain.

    ``reservation`` is present if and only if ``outcome`` is CLOSE_CANDIDATE, so a
    KEEP-shaped result can never carry reservation data and a CLOSE-shaped result can
    never lack it. Construct via ``keep``/``close`` rather than the bare constructor.
    """

    work_item_id: str
    outcome: PositionExitEvaluationOutcome
    evaluation_persistence: OrdinaryClosePersistenceResult
    reservation: OrdinaryCloseReservationPersistenceResult | None

    def __post_init__(self) -> None:
        if type(self.work_item_id) is not str or not self.work_item_id.strip():
            raise ValueError("work_item_id must be a non-blank exact str")
        if type(self.outcome) is not PositionExitEvaluationOutcome:
            raise TypeError("outcome must be exact PositionExitEvaluationOutcome")
        if type(self.evaluation_persistence) is not OrdinaryClosePersistenceResult:
            raise TypeError(
                "evaluation_persistence must be exact OrdinaryClosePersistenceResult"
            )
        OrdinaryClosePersistenceResult.__post_init__(self.evaluation_persistence)
        evaluated = self.evaluation_persistence.result
        if evaluated.work_item_id != self.work_item_id:
            raise ValueError("evaluation_persistence does not belong to this work item")
        if evaluated.evaluation.outcome is not self.outcome:
            raise ValueError("outcome does not match the persisted evaluation outcome")
        if self.outcome is PositionExitEvaluationOutcome.KEEP:
            if self.reservation is not None:
                raise ValueError("KEEP result cannot carry reservation data")
        else:
            if type(self.reservation) is not OrdinaryCloseReservationPersistenceResult:
                raise TypeError(
                    "CLOSE_CANDIDATE result requires exact reservation persistence"
                )
            OrdinaryCloseReservationPersistenceResult.__post_init__(self.reservation)
            candidate = evaluated.evaluation.close_candidate
            if (
                candidate is None
                or self.reservation.portfolio_decision.close_candidate_id
                != candidate.close_candidate_id
                or self.reservation.portfolio_decision.operational_evaluation_id
                != evaluated.operational_evaluation_id
            ):
                raise ValueError("reservation does not belong to this evaluation's Candidate")

    @classmethod
    def keep(
        cls, *, work_item_id: str, evaluation_persistence: OrdinaryClosePersistenceResult
    ) -> OrdinaryCloseApplicationResult:
        return cls(
            work_item_id,
            PositionExitEvaluationOutcome.KEEP,
            evaluation_persistence,
            None,
        )

    @classmethod
    def close(
        cls,
        *,
        work_item_id: str,
        evaluation_persistence: OrdinaryClosePersistenceResult,
        reservation: OrdinaryCloseReservationPersistenceResult,
    ) -> OrdinaryCloseApplicationResult:
        return cls(
            work_item_id,
            PositionExitEvaluationOutcome.CLOSE_CANDIDATE,
            evaluation_persistence,
            reservation,
        )

    @property
    def approved_intent(self) -> ApprovedCloseIntent | None:
        return None if self.reservation is None else self.reservation.intent


class OrdinaryCloseApplicationService:
    """Compose B3 evaluation persistence and B4 reservation for one Position."""

    def __init__(self, *, persistence_store: SQLiteOrdinaryCloseStore) -> None:
        self._persistence_store = persistence_store

    def run(
        self,
        work_item: OrdinaryPositionExitWorkItem,
        *,
        config: NewsFilteredCarryStrategyConfig,
    ) -> OrdinaryCloseApplicationResult:
        _prevalidate(work_item, config)

        evaluation_persistence = self._persistence_store.evaluate_and_persist(
            work_item, config=config
        )
        if type(evaluation_persistence) is not OrdinaryClosePersistenceResult:
            raise TypeError(
                "evaluation_persistence must be exact OrdinaryClosePersistenceResult"
            )
        OrdinaryClosePersistenceResult.__post_init__(evaluation_persistence)
        evaluated = evaluation_persistence.result
        if evaluated.work_item_id != work_item.work_item_id:
            raise RuntimeError("B3 persisted a result for another work item")

        if evaluated.evaluation.outcome is PositionExitEvaluationOutcome.KEEP:
            return OrdinaryCloseApplicationResult.keep(
                work_item_id=work_item.work_item_id,
                evaluation_persistence=evaluation_persistence,
            )

        reservation = self._persistence_store.evaluate_and_persist_reservation(
            evaluated,
            capacity=work_item.capacity,
            allocation_policy=work_item.allocation_policy,
            risk_policy=work_item.risk_policy,
            authority=work_item.authority,
        )
        return OrdinaryCloseApplicationResult.close(
            work_item_id=work_item.work_item_id,
            evaluation_persistence=evaluation_persistence,
            reservation=reservation,
        )


def _prevalidate(work_item: object, config: object) -> None:
    if type(config) is not NewsFilteredCarryStrategyConfig:
        raise TypeError("config must be exact NewsFilteredCarryStrategyConfig")
    NewsFilteredCarryStrategyConfig.__post_init__(config)
    if type(work_item) is not OrdinaryPositionExitWorkItem:
        raise TypeError("work_item must be exact OrdinaryPositionExitWorkItem")
    work_item.validate_intrinsic_integrity()
    if work_item.authority is ExecutionAuthorityMode.LIVE:
        raise ValueError(
            "LIVE authority is rejected before Strategy or durable ordinary-close work"
        )
