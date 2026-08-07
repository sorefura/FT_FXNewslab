from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path

from fx_core import CurrencyPair

from .adoption import (
    AdoptionDecisionType,
    SignalAuthorization,
    StrategyAdoptionDecision,
    StrategyAdoptionPolicy,
    canonical_json,
)
from .adoption_store import SQLiteAdoptionStore
from .execution_authority import ExecutionAuthorityMode
from .live_migrations import migrate_live_database
from .models import PositionId, Side
from .operational_swap import SQLiteOperationalSwapStore
from .strategy import (
    ApprovedCloseIntent,
    NewsFilteredCarryStrategyConfig,
    OperationalPositionExitEvaluationResult,
    OrdinaryCloseAllocationPolicy,
    OrdinaryClosePortfolioDecision,
    OrdinaryClosePortfolioDisposition,
    OrdinaryCloseReservationEntry,
    OrdinaryCloseReservationSnapshot,
    OrdinaryCloseRiskDecision,
    OrdinaryCloseRiskOutcome,
    OrdinaryCloseRiskPolicy,
    OrdinaryCloseRiskReason,
    OrdinaryPositionExitEvaluator,
    OrdinaryPositionExitWorkItem,
    PositionCloseCandidate,
    PositionCloseCapacityEvidence,
    PositionExitEvaluationOutcome,
    SignalAdoptionResolutionOutcome,
    SignalAdoptionTerminalResolution,
    evaluate_ordinary_close_portfolio_and_risk,
)


class OrdinaryClosePersistenceConflict(ValueError):
    pass


class OrdinaryCloseReservationIntegrityError(RuntimeError):
    pass


class OrdinaryClosePersistenceDisposition(StrEnum):
    INSERTED = "INSERTED"
    REUSED_IDENTICAL = "REUSED_IDENTICAL"


class OrdinaryCloseReservationDisposition(StrEnum):
    INSERTED = "INSERTED"
    REUSED_IDENTICAL = "REUSED_IDENTICAL"


@dataclass(frozen=True, slots=True)
class OrdinaryClosePersistenceResult:
    disposition: OrdinaryClosePersistenceDisposition
    result: OperationalPositionExitEvaluationResult

    def __post_init__(self) -> None:
        if type(self.disposition) is not OrdinaryClosePersistenceDisposition:
            raise TypeError(
                "disposition must be exact OrdinaryClosePersistenceDisposition"
            )
        if type(self.result) is not OperationalPositionExitEvaluationResult:
            raise TypeError(
                "result must be exact OperationalPositionExitEvaluationResult"
            )
        OperationalPositionExitEvaluationResult.__post_init__(self.result)


@dataclass(frozen=True, slots=True)
class OrdinaryCloseReservationPersistenceResult:
    disposition: OrdinaryCloseReservationDisposition
    portfolio_decision: OrdinaryClosePortfolioDecision
    risk_decision: OrdinaryCloseRiskDecision
    intent: ApprovedCloseIntent | None

    def __post_init__(self) -> None:
        if type(self.disposition) is not OrdinaryCloseReservationDisposition:
            raise TypeError(
                "disposition must be exact OrdinaryCloseReservationDisposition"
            )
        if type(self.portfolio_decision) is not OrdinaryClosePortfolioDecision:
            raise TypeError("portfolio_decision must be exact OrdinaryClosePortfolioDecision")
        OrdinaryClosePortfolioDecision.__post_init__(self.portfolio_decision)
        if type(self.risk_decision) is not OrdinaryCloseRiskDecision:
            raise TypeError("risk_decision must be exact OrdinaryCloseRiskDecision")
        OrdinaryCloseRiskDecision.__post_init__(self.risk_decision)
        if (
            self.risk_decision.portfolio_decision_id
            != self.portfolio_decision.portfolio_decision_id
        ):
            raise ValueError("risk_decision does not belong to portfolio_decision")
        if self.risk_decision.outcome is OrdinaryCloseRiskOutcome.APPROVE:
            if type(self.intent) is not ApprovedCloseIntent:
                raise TypeError("Risk APPROVE requires exact ApprovedCloseIntent")
            ApprovedCloseIntent.__post_init__(self.intent)
            if self.intent.portfolio_decision_id != self.portfolio_decision.portfolio_decision_id:
                raise ValueError("intent does not belong to portfolio_decision")
        elif self.intent is not None:
            raise ValueError("non-APPROVE Risk outcome cannot carry an Intent")


class SQLiteOrdinaryCloseStore:
    """Authenticate, re-evaluate, and append one ordinary-close exit decision."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            migrate_live_database(connection)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def evaluate_and_persist(
        self,
        work_item: OrdinaryPositionExitWorkItem,
        *,
        config: NewsFilteredCarryStrategyConfig,
    ) -> OrdinaryClosePersistenceResult:
        _validate_inputs(work_item, config)
        if work_item.authority is ExecutionAuthorityMode.LIVE:
            raise ValueError("LIVE authority is prohibited from ordinary close persistence")
        swap_evidence = work_item.swap_resolution.evidence

        with closing(self._connect()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                resolution = work_item.signal_resolution
                if resolution.outcome is SignalAdoptionResolutionOutcome.AUTHORIZED:
                    authorized = resolution.authorized_signal
                    if authorized is None:
                        raise ValueError("AUTHORIZED resolution requires an AuthorizedSignal")
                    authority = SQLiteAdoptionStore.get_authority_on(
                        connection, authorized.authorization.authorization_id
                    )
                    _validate_persisted_signal_authority(
                        config,
                        work_item,
                        authority.authorization,
                        authority.approval,
                        authority.policy,
                        authority.revocations,
                    )
                operational_result = OrdinaryPositionExitEvaluator(config).evaluate(work_item)
                existing_evaluation = connection.execute(
                    "SELECT 1 FROM live_ordinary_close_operational_evaluations "
                    "WHERE operational_evaluation_id = ?",
                    (operational_result.operational_evaluation_id,),
                ).fetchone()
                if existing_evaluation is None:
                    _append_or_compare_config(connection, config)
                    if swap_evidence is not None:
                        SQLiteOperationalSwapStore.append_or_compare_on(
                            connection, swap_evidence
                        )
                    _append_or_compare_capacity(connection, work_item.capacity)
                    _append_or_compare_resolution(connection, resolution)
                    _append_or_compare_work_item(connection, work_item)
                else:
                    _require_existing_config(connection, config)
                    if swap_evidence is not None:
                        persisted_swap = SQLiteOperationalSwapStore.get_exact_on(
                            connection, swap_evidence.swap_evidence_id
                        )
                        if persisted_swap.evidence != swap_evidence:
                            raise OrdinaryClosePersistenceConflict(
                                "replayed work item lacks its exact persisted Swap Evidence"
                            )
                    _require_existing_capacity(connection, work_item.capacity)
                    _require_existing_resolution(connection, resolution)
                    _require_existing_work_item(connection, work_item)
                inserted = _append_or_compare_evaluation(connection, operational_result)
                if inserted is (existing_evaluation is not None):
                    raise OrdinaryClosePersistenceConflict(
                        "evaluation insert disposition changed inside held writer lock"
                    )
                _require_exact_candidate_cardinality(connection, operational_result)
                connection.commit()
            except BaseException:
                if connection.in_transaction:
                    connection.rollback()
                raise
        return OrdinaryClosePersistenceResult(
            OrdinaryClosePersistenceDisposition.INSERTED
            if inserted
            else OrdinaryClosePersistenceDisposition.REUSED_IDENTICAL,
            operational_result,
        )

    def evaluate_and_persist_reservation(
        self,
        evaluation_result: OperationalPositionExitEvaluationResult,
        *,
        capacity: PositionCloseCapacityEvidence,
        allocation_policy: OrdinaryCloseAllocationPolicy,
        risk_policy: OrdinaryCloseRiskPolicy,
        authority: ExecutionAuthorityMode,
    ) -> OrdinaryCloseReservationPersistenceResult:
        """Atomically decide and persist one CLOSE Candidate's Portfolio/Risk/Intent.

        Returns the already-persisted chain unchanged on exact replay, independent of
        any reservations made against the same Position since the original decision.
        """
        candidate = _validate_reservation_inputs(
            evaluation_result, capacity, allocation_policy, risk_policy, authority
        )

        with closing(self._connect()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                existing = _hydrate_existing_reservation_chain(
                    connection, candidate.close_candidate_id
                )
                if existing is not None:
                    connection.commit()
                    portfolio_decision, risk_decision, intent = existing
                    return OrdinaryCloseReservationPersistenceResult(
                        OrdinaryCloseReservationDisposition.REUSED_IDENTICAL,
                        portfolio_decision,
                        risk_decision,
                        intent,
                    )

                work_item_id = _authenticate_operational_evaluation(
                    connection, evaluation_result
                )
                _authenticate_candidate(connection, evaluation_result, candidate)
                _require_existing_capacity(connection, capacity)
                _authenticate_policies_and_authority(
                    connection, work_item_id, capacity, allocation_policy, risk_policy, authority
                )
                if (
                    candidate.position_id != capacity.position_id
                    or candidate.pair != capacity.pair
                    or candidate.existing_position_side is not capacity.existing_position_side
                ):
                    raise OrdinaryClosePersistenceConflict(
                        "supplied capacity does not match Candidate Position/Pair/Side"
                    )

                reservation_snapshot = _read_reservation_snapshot(
                    connection, candidate.position_id
                )
                portfolio_decision, risk_decision, intent = (
                    evaluate_ordinary_close_portfolio_and_risk(
                        evaluation_result,
                        capacity=capacity,
                        reservation_snapshot=reservation_snapshot,
                        allocation_policy=allocation_policy,
                        risk_policy=risk_policy,
                        authority=authority,
                    )
                )
                _insert_portfolio_decision(connection, portfolio_decision)
                _insert_risk_decision(connection, risk_decision)
                if intent is not None:
                    _insert_intent(connection, intent)

                reread = _hydrate_existing_reservation_chain(
                    connection, candidate.close_candidate_id
                )
                if reread != (portfolio_decision, risk_decision, intent):
                    raise OrdinaryClosePersistenceConflict(
                        "freshly persisted reservation chain does not match its own re-read"
                    )
                connection.commit()
            except BaseException:
                if connection.in_transaction:
                    connection.rollback()
                raise
        return OrdinaryCloseReservationPersistenceResult(
            OrdinaryCloseReservationDisposition.INSERTED,
            portfolio_decision,
            risk_decision,
            intent,
        )


def _validate_inputs(work_item: object, config: object) -> None:
    if type(config) is not NewsFilteredCarryStrategyConfig:
        raise TypeError("config must be exact NewsFilteredCarryStrategyConfig")
    NewsFilteredCarryStrategyConfig.__post_init__(config)
    if type(work_item) is not OrdinaryPositionExitWorkItem:
        raise TypeError("work_item must be exact OrdinaryPositionExitWorkItem")
    work_item.validate_intrinsic_integrity()


def _validate_persisted_signal_authority(
    config: NewsFilteredCarryStrategyConfig,
    work_item: OrdinaryPositionExitWorkItem,
    persisted_authorization: SignalAuthorization,
    approval: StrategyAdoptionDecision,
    policy: StrategyAdoptionPolicy,
    revocations: tuple[StrategyAdoptionDecision, ...],
) -> None:
    authorized = work_item.signal_resolution.authorized_signal
    if authorized is None:
        raise ValueError("AUTHORIZED resolution requires an AuthorizedSignal")
    supplied = authorized.authorization
    if persisted_authorization != supplied:
        raise ValueError("supplied authorization differs from persisted authorization")
    if approval.decision_type is not AdoptionDecisionType.APPROVED_FOR_STRATEGY:
        raise ValueError("authorization authority is not an approval")
    if (
        approval.strategy_id != config.strategy_id
        or approval.strategy_version != config.strategy_version
        or approval.strategy_config_identity != config.strategy_config_identity
        or policy.strategy_config_identity != config.strategy_config_identity
    ):
        raise ValueError("persisted adoption does not approve the exact Strategy config")
    signal = authorized.signal
    if not approval.approved_signal_specification.matches_signal(signal):
        raise ValueError("Pair Signal does not match the persisted approved specification")
    evaluated_at = work_item.evaluation_input.evaluated_at
    authority_start = max(approval.effective_from, approval.decided_at)
    if (
        signal.created_at < authority_start
        or supplied.authorized_at < authority_start
        or evaluated_at < authority_start
    ):
        raise ValueError("Signal, authorization, and evaluation must not predate authority")
    if signal.created_at > supplied.authorized_at or supplied.authorized_at > evaluated_at:
        raise ValueError("Signal creation, authorization, and evaluation order is invalid")
    if evaluated_at >= approval.expires_at:
        raise ValueError("approval is expired at the evaluation authority instant")
    if any(revocation.decided_at <= evaluated_at for revocation in revocations):
        raise ValueError("approval is revoked at the evaluation authority instant")


def _append_or_compare_config(
    connection: sqlite3.Connection, config: NewsFilteredCarryStrategyConfig
) -> None:
    config_json = canonical_json(config.identity_payload)
    connection.execute(
        "INSERT OR IGNORE INTO live_news_filtered_carry_configs VALUES (?, ?)",
        (config.strategy_config_identity, config_json),
    )
    row = connection.execute(
        "SELECT config_json FROM live_news_filtered_carry_configs "
        "WHERE strategy_config_identity = ?",
        (config.strategy_config_identity,),
    ).fetchone()
    if row is None or row["config_json"] != config_json:
        raise OrdinaryClosePersistenceConflict(
            "Strategy config identity already has different content"
        )


def _require_existing_config(
    connection: sqlite3.Connection, config: NewsFilteredCarryStrategyConfig
) -> None:
    config_json = canonical_json(config.identity_payload)
    row = connection.execute(
        "SELECT config_json FROM live_news_filtered_carry_configs "
        "WHERE strategy_config_identity = ?",
        (config.strategy_config_identity,),
    ).fetchone()
    if row is None or row["config_json"] != config_json:
        raise OrdinaryClosePersistenceConflict(
            "replayed work item lacks its exact persisted Strategy config"
        )


def _capacity_values(capacity: PositionCloseCapacityEvidence) -> tuple[str, ...]:
    return (
        capacity.capacity_evidence_id,
        capacity.capacity_contract_version,
        capacity.position_id.value,
        capacity.position_evidence_id,
        capacity.pair.symbol,
        capacity.existing_position_side.value,
        capacity.position_observed_at.isoformat(),
        str(capacity.open_quantity),
        capacity.quantity_unit,
        capacity.source,
        capacity.checkpoint_id,
    )


def _append_or_compare_capacity(
    connection: sqlite3.Connection, capacity: PositionCloseCapacityEvidence
) -> None:
    values = _capacity_values(capacity)
    connection.execute(
        "INSERT OR IGNORE INTO live_ordinary_close_capacity_evidence "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        values,
    )
    row = connection.execute(
        "SELECT * FROM live_ordinary_close_capacity_evidence WHERE capacity_evidence_id = ?",
        (capacity.capacity_evidence_id,),
    ).fetchone()
    if row is None or tuple(row) != values:
        raise OrdinaryClosePersistenceConflict(
            "Position close capacity evidence ID already has different content"
        )


def _require_existing_capacity(
    connection: sqlite3.Connection, capacity: PositionCloseCapacityEvidence
) -> None:
    values = _capacity_values(capacity)
    row = connection.execute(
        "SELECT * FROM live_ordinary_close_capacity_evidence WHERE capacity_evidence_id = ?",
        (capacity.capacity_evidence_id,),
    ).fetchone()
    if row is None or tuple(row) != values:
        raise OrdinaryClosePersistenceConflict(
            "replayed work item lacks its exact persisted capacity evidence"
        )


def _resolution_values(resolution: SignalAdoptionTerminalResolution) -> tuple[str | None, ...]:
    authorized = resolution.authorized_signal
    return (
        resolution.resolution_id,
        resolution.outcome.value,
        resolution.signal_selection_checkpoint_id,
        resolution.selection_request_id,
        resolution.selection_claim_id,
        resolution.selection_snapshot_id,
        resolution.selection_completion_id,
        resolution.prior_adoption_decision_id,
        resolution.adoption_state_evidence_id,
        resolution.reason_code,
        resolution.resolved_at.isoformat(),
        None if authorized is None else authorized.signal.signal_id.value,
        None if authorized is None else authorized.authorization.authorization_id,
    )


def _append_or_compare_resolution(
    connection: sqlite3.Connection, resolution: SignalAdoptionTerminalResolution
) -> None:
    values = _resolution_values(resolution)
    connection.execute(
        "INSERT OR IGNORE INTO live_ordinary_close_signal_resolutions "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        values,
    )
    row = connection.execute(
        "SELECT * FROM live_ordinary_close_signal_resolutions WHERE resolution_id = ?",
        (resolution.resolution_id,),
    ).fetchone()
    if row is None or tuple(row) != values:
        raise OrdinaryClosePersistenceConflict(
            "Signal/Adoption resolution ID already has different content"
        )


def _require_existing_resolution(
    connection: sqlite3.Connection, resolution: SignalAdoptionTerminalResolution
) -> None:
    values = _resolution_values(resolution)
    row = connection.execute(
        "SELECT * FROM live_ordinary_close_signal_resolutions WHERE resolution_id = ?",
        (resolution.resolution_id,),
    ).fetchone()
    if row is None or tuple(row) != values:
        raise OrdinaryClosePersistenceConflict(
            "replayed work item lacks its exact persisted Signal/Adoption resolution"
        )


def _work_item_values(work_item: OrdinaryPositionExitWorkItem) -> tuple[str | None, ...]:
    swap_evidence = work_item.swap_resolution.evidence
    inp = work_item.evaluation_input
    return (
        work_item.work_item_id,
        inp.position_id.value,
        inp.pair.symbol,
        inp.existing_position_side.value,
        inp.approved_strategy_config_identity,
        work_item.capacity.capacity_evidence_id,
        work_item.signal_resolution.resolution_id,
        None if swap_evidence is None else swap_evidence.swap_evidence_id,
        work_item.authority.value,
        canonical_json(work_item.identity_payload),
    )


def _append_or_compare_work_item(
    connection: sqlite3.Connection, work_item: OrdinaryPositionExitWorkItem
) -> None:
    values = _work_item_values(work_item)
    connection.execute(
        "INSERT OR IGNORE INTO live_ordinary_close_work_items "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        values,
    )
    row = connection.execute(
        "SELECT * FROM live_ordinary_close_work_items WHERE work_item_id = ?",
        (work_item.work_item_id,),
    ).fetchone()
    if row is None or tuple(row) != values:
        raise OrdinaryClosePersistenceConflict(
            "ordinary close work item ID already has different content"
        )


def _require_existing_work_item(
    connection: sqlite3.Connection, work_item: OrdinaryPositionExitWorkItem
) -> None:
    values = _work_item_values(work_item)
    row = connection.execute(
        "SELECT * FROM live_ordinary_close_work_items WHERE work_item_id = ?",
        (work_item.work_item_id,),
    ).fetchone()
    if row is None or tuple(row) != values:
        raise OrdinaryClosePersistenceConflict(
            "replayed work item lacks its exact persisted work item content"
        )


def _append_or_compare_evaluation(
    connection: sqlite3.Connection,
    operational_result: OperationalPositionExitEvaluationResult,
) -> bool:
    evaluation = operational_result.evaluation
    evaluation_json = canonical_json(evaluation.identity_payload)
    close_candidate = evaluation.close_candidate
    values = (
        operational_result.operational_evaluation_id,
        operational_result.work_item_id,
        evaluation.evaluation_id,
        evaluation.outcome.value,
        None if close_candidate is None else close_candidate.close_candidate_id,
        None if evaluation.keep_reason is None else evaluation.keep_reason.value,
        evaluation_json,
    )
    cursor = connection.execute(
        "INSERT OR IGNORE INTO live_ordinary_close_operational_evaluations "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        values,
    )
    row = connection.execute(
        "SELECT * FROM live_ordinary_close_operational_evaluations "
        "WHERE operational_evaluation_id = ?",
        (operational_result.operational_evaluation_id,),
    ).fetchone()
    if row is None or tuple(row) != values:
        raise OrdinaryClosePersistenceConflict(
            "ordinary close operational evaluation ID already has different content"
        )
    if evaluation.close_candidate is not None:
        candidate_json = canonical_json(evaluation.close_candidate.identity_payload)
        candidate_values = (
            evaluation.close_candidate.close_candidate_id,
            operational_result.operational_evaluation_id,
            candidate_json,
        )
        if cursor.rowcount == 1:
            connection.execute(
                "INSERT INTO live_ordinary_close_candidates VALUES (?, ?, ?)",
                candidate_values,
            )
        candidate_row = connection.execute(
            "SELECT * FROM live_ordinary_close_candidates WHERE operational_evaluation_id = ?",
            (operational_result.operational_evaluation_id,),
        ).fetchone()
        if candidate_row is None or tuple(candidate_row) != candidate_values:
            raise OrdinaryClosePersistenceConflict(
                "ordinary close CLOSE_CANDIDATE evaluation lacks its exact persisted Candidate"
            )
    return cursor.rowcount == 1


def _require_exact_candidate_cardinality(
    connection: sqlite3.Connection,
    operational_result: OperationalPositionExitEvaluationResult,
) -> None:
    count = int(
        connection.execute(
            "SELECT COUNT(*) FROM live_ordinary_close_candidates "
            "WHERE operational_evaluation_id = ?",
            (operational_result.operational_evaluation_id,),
        ).fetchone()[0]
    )
    expected = (
        1
        if operational_result.evaluation.outcome is PositionExitEvaluationOutcome.CLOSE_CANDIDATE
        else 0
    )
    if count != expected:
        raise OrdinaryClosePersistenceConflict(
            "persisted Candidate cardinality does not match evaluation outcome"
        )


# ---------------------------------------------------------------------------
# B4 - atomic Portfolio/Risk decision and capacity reservation
# ---------------------------------------------------------------------------


def _validate_reservation_inputs(
    evaluation_result: object,
    capacity: object,
    allocation_policy: object,
    risk_policy: object,
    authority: object,
) -> PositionCloseCandidate:
    if type(evaluation_result) is not OperationalPositionExitEvaluationResult:
        raise TypeError(
            "evaluation_result must be exact OperationalPositionExitEvaluationResult"
        )
    OperationalPositionExitEvaluationResult.__post_init__(evaluation_result)
    if evaluation_result.evaluation.outcome is not PositionExitEvaluationOutcome.CLOSE_CANDIDATE:
        raise ValueError(
            "ordinary close reservation persistence requires a CLOSE_CANDIDATE evaluation"
        )
    candidate = evaluation_result.evaluation.close_candidate
    if candidate is None:
        raise ValueError("CLOSE_CANDIDATE evaluation requires a close_candidate")
    if type(capacity) is not PositionCloseCapacityEvidence:
        raise TypeError("capacity must be exact PositionCloseCapacityEvidence")
    PositionCloseCapacityEvidence.__post_init__(capacity)
    if type(allocation_policy) is not OrdinaryCloseAllocationPolicy:
        raise TypeError("allocation_policy must be exact OrdinaryCloseAllocationPolicy")
    OrdinaryCloseAllocationPolicy.__post_init__(allocation_policy)
    if type(risk_policy) is not OrdinaryCloseRiskPolicy:
        raise TypeError("risk_policy must be exact OrdinaryCloseRiskPolicy")
    OrdinaryCloseRiskPolicy.__post_init__(risk_policy)
    if type(authority) is not ExecutionAuthorityMode:
        raise TypeError("authority must be exact ExecutionAuthorityMode")
    if authority is ExecutionAuthorityMode.LIVE:
        raise ValueError(
            "LIVE authority is prohibited from ordinary close reservation persistence"
        )
    return candidate


def _authenticate_operational_evaluation(
    connection: sqlite3.Connection,
    evaluation_result: OperationalPositionExitEvaluationResult,
) -> str:
    row = connection.execute(
        "SELECT * FROM live_ordinary_close_operational_evaluations "
        "WHERE operational_evaluation_id = ?",
        (evaluation_result.operational_evaluation_id,),
    ).fetchone()
    if (
        row is None
        or row["work_item_id"] != evaluation_result.work_item_id
        or row["outcome"] != PositionExitEvaluationOutcome.CLOSE_CANDIDATE.value
        or row["evaluation_json"] != canonical_json(evaluation_result.evaluation.identity_payload)
    ):
        raise OrdinaryClosePersistenceConflict(
            "reservation request lacks its exact persisted operational evaluation"
        )
    return str(row["work_item_id"])


def _authenticate_candidate(
    connection: sqlite3.Connection,
    evaluation_result: OperationalPositionExitEvaluationResult,
    candidate: PositionCloseCandidate,
) -> None:
    row = connection.execute(
        "SELECT * FROM live_ordinary_close_candidates WHERE close_candidate_id = ?",
        (candidate.close_candidate_id,),
    ).fetchone()
    if (
        row is None
        or row["operational_evaluation_id"] != evaluation_result.operational_evaluation_id
        or row["candidate_json"] != canonical_json(candidate.identity_payload)
    ):
        raise OrdinaryClosePersistenceConflict(
            "reservation request lacks its exact persisted close Candidate"
        )


def _authenticate_policies_and_authority(
    connection: sqlite3.Connection,
    work_item_id: str,
    capacity: PositionCloseCapacityEvidence,
    allocation_policy: OrdinaryCloseAllocationPolicy,
    risk_policy: OrdinaryCloseRiskPolicy,
    authority: ExecutionAuthorityMode,
) -> None:
    row = connection.execute(
        "SELECT work_item_json, authority, capacity_evidence_id "
        "FROM live_ordinary_close_work_items WHERE work_item_id = ?",
        (work_item_id,),
    ).fetchone()
    if row is None:
        raise OrdinaryClosePersistenceConflict(
            "reservation request lacks its exact persisted work item"
        )
    payload = json.loads(row["work_item_json"])
    expected_allocation = {
        "version": allocation_policy.policy_version,
        "target_fraction": str(allocation_policy.target_fraction),
    }
    expected_risk = {
        "version": risk_policy.policy_version,
        "maximum_capacity_age_us": int(
            risk_policy.maximum_capacity_age.total_seconds() * 1_000_000
        ),
    }
    if (
        payload.get("allocation") != expected_allocation
        or payload.get("risk") != expected_risk
        or row["authority"] != authority.value
    ):
        raise OrdinaryClosePersistenceConflict(
            "reservation request policies/authority do not match the persisted work item"
        )
    if row["capacity_evidence_id"] != capacity.capacity_evidence_id:
        raise OrdinaryClosePersistenceConflict(
            "supplied capacity is not the capacity bound to this Candidate's work item"
        )


def _read_reservation_snapshot(
    connection: sqlite3.Connection, position_id: PositionId
) -> OrdinaryCloseReservationSnapshot:
    # Ordered by intent_seq (an INTEGER PRIMARY KEY AUTOINCREMENT), a monotonic
    # sequence SQLite assigns at commit time and never reuses. This gives a stable
    # total order even when two Intents share the same created_at, unlike sorting
    # by Decimal quantity or any string column.
    rows = connection.execute(
        "SELECT idempotency_key, quantity FROM live_ordinary_close_approved_intents "
        "WHERE position_id = ? ORDER BY intent_seq ASC",
        (position_id.value,),
    ).fetchall()
    entries = tuple(
        OrdinaryCloseReservationEntry(row["idempotency_key"], Decimal(row["quantity"]))
        for row in rows
    )
    return OrdinaryCloseReservationSnapshot(position_id, entries)


def _insert_portfolio_decision(
    connection: sqlite3.Connection, decision: OrdinaryClosePortfolioDecision
) -> None:
    snapshot_json = json.dumps(
        [
            {"intent_id": entry.intent_id, "quantity": str(entry.quantity)}
            for entry in decision.reservation_snapshot.entries
        ]
    )
    connection.execute(
        "INSERT INTO live_ordinary_close_portfolio_decisions VALUES "
        "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            decision.portfolio_decision_id,
            decision.close_candidate_id,
            decision.operational_evaluation_id,
            decision.capacity_evidence_id,
            decision.allocation_policy.policy_version,
            str(decision.allocation_policy.target_fraction),
            decision.reservation_snapshot.position_id.value,
            snapshot_json,
            str(decision.target_quantity),
            str(decision.available_before),
            decision.disposition.value,
            None if decision.allocated_quantity is None else str(decision.allocated_quantity),
        ),
    )


def _insert_risk_decision(
    connection: sqlite3.Connection, decision: OrdinaryCloseRiskDecision
) -> None:
    connection.execute(
        "INSERT INTO live_ordinary_close_risk_decisions VALUES (?, ?, ?, ?, ?, ?)",
        (
            decision.risk_decision_id,
            decision.portfolio_decision_id,
            decision.risk_policy.policy_version,
            int(decision.risk_policy.maximum_capacity_age.total_seconds() * 1_000_000),
            decision.outcome.value,
            decision.reason.value,
        ),
    )


def _insert_intent(connection: sqlite3.Connection, intent: ApprovedCloseIntent) -> None:
    connection.execute(
        "INSERT INTO live_ordinary_close_approved_intents "
        "(close_candidate_id, portfolio_decision_id, risk_decision_id, capacity_evidence_id, "
        "position_id, pair, side, quantity, authority, idempotency_key, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            intent.close_candidate_id,
            intent.portfolio_decision_id,
            intent.risk_decision_id,
            intent.capacity_evidence_id,
            intent.position_id.value,
            intent.pair.symbol,
            intent.side.value,
            str(intent.quantity),
            intent.authority.value,
            intent.idempotency_key,
            intent.created_at.isoformat(),
        ),
    )


def _hydrate_portfolio_decision(row: sqlite3.Row) -> OrdinaryClosePortfolioDecision:
    try:
        entries = tuple(
            OrdinaryCloseReservationEntry(entry["intent_id"], Decimal(entry["quantity"]))
            for entry in json.loads(row["reservation_snapshot_json"])
        )
        return OrdinaryClosePortfolioDecision(
            row["portfolio_decision_id"],
            row["close_candidate_id"],
            row["operational_evaluation_id"],
            row["capacity_evidence_id"],
            OrdinaryCloseAllocationPolicy(
                row["allocation_policy_version"], Decimal(row["target_fraction"])
            ),
            OrdinaryCloseReservationSnapshot(PositionId(row["position_id"]), entries),
            Decimal(row["target_quantity"]),
            Decimal(row["available_before"]),
            OrdinaryClosePortfolioDisposition(row["disposition"]),
            None if row["allocated_quantity"] is None else Decimal(row["allocated_quantity"]),
        )
    except (KeyError, TypeError, ValueError, InvalidOperation) as error:
        raise OrdinaryCloseReservationIntegrityError(
            "persisted ordinary close Portfolio decision is malformed"
        ) from error


def _hydrate_risk_decision(row: sqlite3.Row) -> OrdinaryCloseRiskDecision:
    try:
        return OrdinaryCloseRiskDecision(
            row["risk_decision_id"],
            row["portfolio_decision_id"],
            OrdinaryCloseRiskPolicy(
                row["risk_policy_version"],
                timedelta(microseconds=int(row["maximum_capacity_age_us"])),
            ),
            OrdinaryCloseRiskOutcome(row["outcome"]),
            OrdinaryCloseRiskReason(row["reason"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise OrdinaryCloseReservationIntegrityError(
            "persisted ordinary close Risk decision is malformed"
        ) from error


def _hydrate_intent(row: sqlite3.Row) -> ApprovedCloseIntent:
    try:
        return ApprovedCloseIntent(
            row["close_candidate_id"],
            row["portfolio_decision_id"],
            row["risk_decision_id"],
            row["capacity_evidence_id"],
            PositionId(row["position_id"]),
            CurrencyPair.parse(row["pair"]),
            Side(row["side"]),
            Decimal(row["quantity"]),
            ExecutionAuthorityMode(row["authority"]),
            row["idempotency_key"],
            datetime.fromisoformat(row["created_at"]),
        )
    except (KeyError, TypeError, ValueError, InvalidOperation) as error:
        raise OrdinaryCloseReservationIntegrityError(
            "persisted Approved close Intent is malformed"
        ) from error


def _hydrate_existing_reservation_chain(
    connection: sqlite3.Connection, close_candidate_id: str
) -> (
    tuple[OrdinaryClosePortfolioDecision, OrdinaryCloseRiskDecision, ApprovedCloseIntent | None]
    | None
):
    portfolio_row = connection.execute(
        "SELECT * FROM live_ordinary_close_portfolio_decisions WHERE close_candidate_id = ?",
        (close_candidate_id,),
    ).fetchone()
    if portfolio_row is None:
        return None
    portfolio_decision = _hydrate_portfolio_decision(portfolio_row)

    risk_row = connection.execute(
        "SELECT * FROM live_ordinary_close_risk_decisions WHERE portfolio_decision_id = ?",
        (portfolio_decision.portfolio_decision_id,),
    ).fetchone()
    if risk_row is None:
        raise OrdinaryCloseReservationIntegrityError(
            "persisted Portfolio decision lacks its Risk decision"
        )
    risk_decision = _hydrate_risk_decision(risk_row)

    intent_row = connection.execute(
        "SELECT * FROM live_ordinary_close_approved_intents WHERE portfolio_decision_id = ?",
        (portfolio_decision.portfolio_decision_id,),
    ).fetchone()
    intent = None if intent_row is None else _hydrate_intent(intent_row)
    if (risk_decision.outcome is OrdinaryCloseRiskOutcome.APPROVE) != (intent is not None):
        raise OrdinaryCloseReservationIntegrityError(
            "persisted Intent cardinality does not match its Risk decision outcome"
        )
    return portfolio_decision, risk_decision, intent
