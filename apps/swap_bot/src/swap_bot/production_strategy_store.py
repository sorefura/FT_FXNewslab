from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from fx_signal_store import (
    PairSignalMaterializerOutcome,
    PairSignalMaterializerResult,
    reconstruct_materialized_pair_signal,
)

from .adoption import (
    AdoptionDecisionType,
    AuthorizedSignal,
    RuntimeMode,
    SignalAuthorization,
    StrategyAdoptionDecision,
    StrategyAdoptionPolicy,
    canonical_json,
)
from .adoption_store import SQLiteAdoptionStore
from .live_migrations import migrate_live_database
from .operational_swap import (
    OperationalSwapResolution,
    OperationalSwapResolutionOutcome,
    SQLiteOperationalSwapStore,
)
from .strategy import (
    EntryEvaluationOutcome,
    NewsFilteredCarryStrategy,
    NewsFilteredCarryStrategyConfig,
    ProductionEntryEvaluation,
    ProductionEntryEvaluationInput,
)


class ProductionEntryPersistenceConflict(ValueError):
    pass


class ProductionEntryPersistenceDisposition(StrEnum):
    INSERTED = "INSERTED"
    REUSED_IDENTICAL = "REUSED_IDENTICAL"


@dataclass(frozen=True, slots=True)
class ProductionEntryPersistenceResult:
    disposition: ProductionEntryPersistenceDisposition
    evaluation: ProductionEntryEvaluation

    def __post_init__(self) -> None:
        if type(self.disposition) is not ProductionEntryPersistenceDisposition:
            raise TypeError(
                "disposition must be exact ProductionEntryPersistenceDisposition"
            )
        if type(self.evaluation) is not ProductionEntryEvaluation:
            raise TypeError("evaluation must be exact ProductionEntryEvaluation")
        ProductionEntryEvaluation.__post_init__(self.evaluation)


class SQLiteProductionEntryStore:
    """Authenticate, evaluate, and append one production-entry decision atomically."""

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
        *,
        config: NewsFilteredCarryStrategyConfig,
        materializer_result: PairSignalMaterializerResult,
        swap_resolution: OperationalSwapResolution,
        evaluation_input: ProductionEntryEvaluationInput,
    ) -> ProductionEntryPersistenceResult:
        _validate_inputs(config, materializer_result, swap_resolution, evaluation_input)
        if (
            evaluation_input.authorized_pair_signal.authorization.runtime_mode
            is not RuntimeMode.SHADOW
        ):
            raise ValueError("LIVE authority is prohibited from production entry persistence")
        signal = reconstruct_materialized_pair_signal(materializer_result)
        if signal != evaluation_input.authorized_pair_signal.signal:
            raise ValueError(
                "Authorized Signal content differs from materialization evidence"
            )
        snapshot = materializer_result.pair_signal_snapshot
        if snapshot is None:
            raise ValueError("selected materialization must contain a Pair Signal snapshot")
        swap_evidence = swap_resolution.evidence
        if swap_evidence is None:
            raise ValueError("EVIDENCE resolution is missing exact Swap Evidence")

        with closing(self._connect()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                authority = SQLiteAdoptionStore.get_authority_on(
                    connection,
                    evaluation_input.authorized_pair_signal.authorization.authorization_id,
                )
                _validate_persisted_authority(
                    config,
                    evaluation_input,
                    authority.authorization,
                    authority.approval,
                    authority.policy,
                    authority.revocations,
                )
                evaluation = NewsFilteredCarryStrategy(config).evaluate_entry(
                    evaluation_input
                )
                existing_evaluation = connection.execute(
                    "SELECT 1 FROM live_production_entry_evaluations "
                    "WHERE evaluation_id = ?",
                    (evaluation.evaluation_id,),
                ).fetchone()
                if existing_evaluation is None:
                    SQLiteOperationalSwapStore.append_or_compare_on(
                        connection, swap_evidence
                    )
                    _append_or_compare_config(connection, config)
                else:
                    _require_existing_config(connection, config)
                    persisted_swap = SQLiteOperationalSwapStore.get_exact_on(
                        connection, swap_evidence.swap_evidence_id
                    )
                    if persisted_swap.evidence != swap_evidence:
                        raise ProductionEntryPersistenceConflict(
                            "replayed evaluation lacks its exact persisted Swap Evidence"
                        )
                inserted = _append_or_compare_evaluation(
                    connection,
                    evaluation,
                    materialization_request_id=materializer_result.request.request_id,
                    pair_signal_content_hash=snapshot.signal_content_hash,
                )
                if inserted is (existing_evaluation is not None):
                    raise ProductionEntryPersistenceConflict(
                        "evaluation insert disposition changed inside held writer lock"
                    )
                _require_exact_candidate_cardinality(connection, evaluation)
                connection.commit()
            except BaseException:
                if connection.in_transaction:
                    connection.rollback()
                raise
        return ProductionEntryPersistenceResult(
            ProductionEntryPersistenceDisposition.INSERTED
            if inserted
            else ProductionEntryPersistenceDisposition.REUSED_IDENTICAL,
            evaluation,
        )


def _validate_inputs(
    config: object,
    materializer_result: object,
    swap_resolution: object,
    evaluation_input: object,
) -> None:
    if type(config) is not NewsFilteredCarryStrategyConfig:
        raise TypeError("config must be exact NewsFilteredCarryStrategyConfig")
    NewsFilteredCarryStrategyConfig.__post_init__(config)
    if type(materializer_result) is not PairSignalMaterializerResult:
        raise TypeError("materializer_result must be exact PairSignalMaterializerResult")
    PairSignalMaterializerResult.validate_intrinsic_integrity(materializer_result)
    if materializer_result.outcome not in (
        PairSignalMaterializerOutcome.MATERIALIZED,
        PairSignalMaterializerOutcome.REUSED_IDENTICAL,
    ):
        raise ValueError("production entry requires selected materialization EVIDENCE")
    if type(evaluation_input) is not ProductionEntryEvaluationInput:
        raise TypeError("evaluation_input must be exact ProductionEntryEvaluationInput")
    ProductionEntryEvaluationInput.__post_init__(evaluation_input)
    if type(swap_resolution) is not OperationalSwapResolution:
        raise TypeError("swap_resolution must be exact OperationalSwapResolution")
    OperationalSwapResolution.validate_intrinsic_integrity(swap_resolution)
    if swap_resolution.outcome is not OperationalSwapResolutionOutcome.EVIDENCE:
        raise ValueError("production entry persistence accepts only EVIDENCE resolution")
    if swap_resolution.evidence != evaluation_input.swap_evidence:
        raise ValueError("evaluation input does not contain the resolved exact Swap Evidence")
    if swap_resolution.pair != evaluation_input.evaluated_pair:
        raise ValueError("Swap resolution belongs to another Pair")
    if materializer_result.request.pair != evaluation_input.evaluated_pair:
        raise ValueError("materialization request belongs to another Pair")
    if evaluation_input.approved_strategy_config_identity != config.strategy_config_identity:
        raise ValueError("evaluation input does not name the supplied Strategy config")


def _validate_persisted_authority(
    config: NewsFilteredCarryStrategyConfig,
    evaluation_input: ProductionEntryEvaluationInput,
    persisted_authorization: SignalAuthorization,
    approval: StrategyAdoptionDecision,
    policy: StrategyAdoptionPolicy,
    revocations: tuple[StrategyAdoptionDecision, ...],
) -> None:
    supplied = evaluation_input.authorized_pair_signal.authorization
    if persisted_authorization != supplied:
        raise ValueError("supplied authorization differs from persisted authorization")
    if type(evaluation_input.authorized_pair_signal) is not AuthorizedSignal:
        raise TypeError("entry input must contain exact AuthorizedSignal")
    if approval.decision_type is not AdoptionDecisionType.APPROVED_FOR_STRATEGY:
        raise ValueError("authorization authority is not an approval")
    if (
        approval.strategy_id != config.strategy_id
        or approval.strategy_version != config.strategy_version
        or approval.strategy_config_identity != config.strategy_config_identity
        or policy.strategy_config_identity != config.strategy_config_identity
    ):
        raise ValueError("persisted adoption does not approve the exact Strategy config")
    signal = evaluation_input.authorized_pair_signal.signal
    if not approval.approved_signal_specification.matches_signal(signal):
        raise ValueError("Pair Signal does not match the persisted approved specification")
    authority_start = max(approval.effective_from, approval.decided_at)
    evaluated_at = evaluation_input.evaluated_at
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
        raise ProductionEntryPersistenceConflict(
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
        raise ProductionEntryPersistenceConflict(
            "replayed evaluation lacks its exact persisted Strategy config"
        )


def _append_or_compare_evaluation(
    connection: sqlite3.Connection,
    evaluation: ProductionEntryEvaluation,
    *,
    materialization_request_id: str,
    pair_signal_content_hash: str,
) -> bool:
    evaluation_json = canonical_json(evaluation.identity_payload)
    values = (
        evaluation.evaluation_id,
        evaluation.strategy_config_identity,
        evaluation.pair.symbol,
        evaluation.signal_id.value,
        evaluation.authorization_id,
        materialization_request_id,
        pair_signal_content_hash,
        evaluation.swap_evidence_id,
        evaluation.outcome.value,
        None if evaluation.skip_reason is None else evaluation.skip_reason.value,
        evaluation_json,
    )
    cursor = connection.execute(
        "INSERT OR IGNORE INTO live_production_entry_evaluations "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        values,
    )
    row = connection.execute(
        "SELECT * FROM live_production_entry_evaluations WHERE evaluation_id = ?",
        (evaluation.evaluation_id,),
    ).fetchone()
    if row is None or tuple(row) != values:
        raise ProductionEntryPersistenceConflict(
            "Production entry evaluation ID already has different content"
        )
    if evaluation.candidate is not None:
        candidate_json = canonical_json(evaluation.candidate.identity_payload)
        candidate_values = (
            evaluation.candidate.candidate_id,
            evaluation.evaluation_id,
            candidate_json,
        )
        if cursor.rowcount == 1:
            connection.execute(
                "INSERT INTO live_production_trade_candidates VALUES (?, ?, ?)",
                candidate_values,
            )
        candidate_row = connection.execute(
            "SELECT * FROM live_production_trade_candidates WHERE strategy_evaluation_id = ?",
            (evaluation.evaluation_id,),
        ).fetchone()
        if candidate_row is None or tuple(candidate_row) != candidate_values:
            raise ProductionEntryPersistenceConflict(
                "Production CANDIDATE evaluation lacks its exact persisted Candidate"
            )
    return cursor.rowcount == 1


def _require_exact_candidate_cardinality(
    connection: sqlite3.Connection, evaluation: ProductionEntryEvaluation
) -> None:
    count = int(
        connection.execute(
            "SELECT COUNT(*) FROM live_production_trade_candidates "
            "WHERE strategy_evaluation_id = ?",
            (evaluation.evaluation_id,),
        ).fetchone()[0]
    )
    expected = 1 if evaluation.outcome is EntryEvaluationOutcome.CANDIDATE else 0
    if count != expected:
        raise ProductionEntryPersistenceConflict(
            "persisted Candidate cardinality does not match evaluation outcome"
        )
