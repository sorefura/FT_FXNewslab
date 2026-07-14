import hashlib
import json
import sqlite3
from collections import defaultdict
from collections.abc import Mapping
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any

from fx_core import Horizon, SignalId
from fx_core.time import require_utc

from .evaluation import (
    CohortEvaluation,
    CohortIdentity,
    EvaluationConfiguration,
    EvaluationExclusionReason,
    EvaluationInputSnapshot,
    EvaluationSample,
    ExcludedForwardObservation,
    ValidationAssessment,
    ValidationPolicy,
)
from .forward import FORWARD_HORIZONS, ForwardJobStatus
from .forward_persistence import SQLiteForwardEvaluationStore


@dataclass(frozen=True, slots=True)
class StoredEvaluationReport:
    report_id: str
    run_id: str
    cohort_id: str
    cohort_identity: Mapping[str, Any]
    metrics: Mapping[str, Any]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class StoredEvaluationRun:
    run_id: str
    ordered_input_identity: tuple[tuple[str, str], ...]
    reports: tuple[StoredEvaluationReport, ...]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class AppendEvaluationRunResult:
    run: StoredEvaluationRun
    created: bool


class SQLiteEvaluationStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        SQLiteForwardEvaluationStore(self.path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def capture_inputs(self) -> EvaluationInputSnapshot:
        with closing(self._connect()) as connection:
            connection.execute("BEGIN")
            signals = connection.execute(
                "SELECT id, target_type, target_value FROM signals ORDER BY created_at, id"
            ).fetchall()
            rows = connection.execute(_EVALUATION_INPUT_QUERY).fetchall()
            result_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM research_forward_results"
                ).fetchone()[0]
            )

        samples: list[EvaluationSample] = []
        exclusions: list[ExcludedForwardObservation] = []
        horizons_by_signal: dict[str, set[Horizon]] = defaultdict(set)
        for row in rows:
            horizons_by_signal[row["signal_id"]].add(Horizon(row["forward_horizon"]))
            cohort = self._cohort(row)
            status = ForwardJobStatus(row["job_status"])
            if status is ForwardJobStatus.COMPLETED:
                self._validate_completed_row(row)
                samples.append(self._sample(row, cohort))
            else:
                exclusions.append(
                    ExcludedForwardObservation(
                        signal_id=SignalId(row["signal_id"]),
                        job_id=row["job_id"],
                        cohort=cohort,
                        reason=_status_exclusion(status),
                    )
                )
        if result_count != len(samples):
            raise ValueError("completed ForwardResult input set is not fully linked")
        unsupported = []
        incomplete = []
        expected_horizons = set(FORWARD_HORIZONS)
        for row in signals:
            signal_id = SignalId(row["id"])
            if not _supported_target(row["target_type"], row["target_value"]):
                unsupported.append(signal_id)
            elif not expected_horizons.issubset(horizons_by_signal[signal_id.value]):
                incomplete.append(signal_id)
        return EvaluationInputSnapshot(
            signals_scanned=len(signals),
            completed_results_scanned=result_count,
            samples=tuple(sorted(samples, key=lambda item: item.input_identity)),
            exclusions=tuple(
                sorted(exclusions, key=lambda item: (item.cohort.cohort_id, item.job_id))
            ),
            unsupported_signal_ids=tuple(unsupported),
            incomplete_horizon_signal_ids=tuple(incomplete),
        )

    def append_run(
        self,
        snapshot: EvaluationInputSnapshot,
        evaluations: tuple[CohortEvaluation, ...],
        configuration: EvaluationConfiguration,
        *,
        created_at: datetime,
    ) -> AppendEvaluationRunResult:
        require_utc(created_at, "Evaluation Run created_at")
        self._validate_evaluations(snapshot, evaluations)
        ordered_hash = _digest(snapshot.ordered_input_identity)
        run_id = "evaluation-run-" + _digest(
            {
                "ordered_input_identity": snapshot.ordered_input_identity,
                "configuration": configuration.identity_payload(),
            }
        )
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO research_evaluation_runs VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    run_id,
                    configuration.evaluator_version,
                    configuration.score_definition_version,
                    configuration.cohort_definition_version,
                    ordered_hash,
                    _canonical_json(configuration.metric.identity_payload()),
                    _canonical_json(configuration.bootstrap.identity_payload()),
                    created_at.isoformat(),
                ),
            )
            created = cursor.rowcount == 1
            if created:
                cohort_by_input = {
                    item.input_identity: item.cohort.cohort_id
                    for item in snapshot.samples
                }
                connection.executemany(
                    "INSERT INTO research_evaluation_run_inputs VALUES (?, ?, ?, ?, ?)",
                    (
                        (
                            run_id,
                            ordinal,
                            signal_id,
                            result_id,
                            cohort_by_input[(signal_id, result_id)],
                        )
                        for ordinal, (signal_id, result_id) in enumerate(
                            snapshot.ordered_input_identity
                        )
                    ),
                )
                connection.executemany(
                    "INSERT INTO research_evaluation_reports VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        (
                            _report_id(run_id, evaluation.cohort.cohort_id),
                            run_id,
                            evaluation.cohort.cohort_id,
                            _canonical_json(evaluation.cohort.identity_payload()),
                            _canonical_json(asdict(evaluation.metrics)),
                            created_at.isoformat(),
                        )
                        for evaluation in evaluations
                    ),
                )
        return AppendEvaluationRunResult(self.get_run(run_id), created)

    def get_run(self, run_id: str) -> StoredEvaluationRun:
        with closing(self._connect()) as connection:
            run = connection.execute(
                "SELECT * FROM research_evaluation_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if run is None:
                raise KeyError(run_id)
            inputs = connection.execute(
                """
                SELECT signal_id, forward_result_id
                FROM research_evaluation_run_inputs
                WHERE run_id = ? ORDER BY ordinal
                """,
                (run_id,),
            ).fetchall()
            reports = connection.execute(
                "SELECT * FROM research_evaluation_reports "
                "WHERE run_id = ? ORDER BY cohort_id",
                (run_id,),
            ).fetchall()
        return StoredEvaluationRun(
            run_id=run_id,
            ordered_input_identity=tuple(
                (row["signal_id"], row["forward_result_id"]) for row in inputs
            ),
            reports=tuple(self._report(row) for row in reports),
            created_at=datetime.fromisoformat(run["created_at"]),
        )

    def append_policy(
        self, policy: ValidationPolicy, *, created_at: datetime
    ) -> bool:
        require_utc(created_at, "Validation Policy created_at")
        payload = _canonical_json(policy.identity_payload())
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO research_validation_policies VALUES (?, ?, ?, ?)",
                (
                    policy.policy_version,
                    policy.content_hash,
                    payload,
                    created_at.isoformat(),
                ),
            )
            row = connection.execute(
                "SELECT content_hash FROM research_validation_policies "
                "WHERE policy_version = ?",
                (policy.policy_version,),
            ).fetchone()
            if row is None or row["content_hash"] != policy.content_hash:
                raise ValueError("validation policy version already has different content")
        return cursor.rowcount == 1

    def append_assessment(self, assessment: ValidationAssessment) -> bool:
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO research_validation_assessments VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    assessment.assessment_id,
                    assessment.evaluation_run_id,
                    assessment.report_id,
                    assessment.policy_version,
                    assessment.policy_content_hash,
                    assessment.status.value,
                    _canonical_json(assessment.condition_results),
                    assessment.created_at.isoformat(),
                ),
            )
        return cursor.rowcount == 1

    @staticmethod
    def _cohort(row: sqlite3.Row) -> CohortIdentity:
        scorer_version = row["scorer_version"]
        if not isinstance(scorer_version, str) or not scorer_version:
            raise ValueError("persisted Signal has no scorer version")
        return CohortIdentity(
            signal_type=row["signal_type"],
            target_type=row["target_type"],
            target_value=row["target_value"],
            signal_horizon=Horizon(row["signal_horizon"]),
            forward_horizon=Horizon(row["forward_horizon"]),
            producer_version=row["producer_version"],
            model_version=row["model_version"],
            prompt_version=row["prompt_version"],
            scorer_version=scorer_version,
            transformation_version=row["transformation_version"],
            market_source=row["job_market_source"],
            market_data_version=row["job_market_data_version"],
            price_basis=row["job_price_basis"],
            granularity=row["job_granularity"],
            projection_version=row["job_projection_version"],
            formula_version=row["job_formula_version"],
        )

    @staticmethod
    def _sample(row: sqlite3.Row, cohort: CohortIdentity) -> EvaluationSample:
        return EvaluationSample(
            signal_id=SignalId(row["signal_id"]),
            forward_result_id=row["forward_result_id"],
            cohort=cohort,
            score=float(row["signal_direction"]),
            target_return_bps=Decimal(row["target_return_bps"]),
            mfe_bps=Decimal(row["mfe_bps"]) if row["mfe_bps"] is not None else None,
            mae_bps=Decimal(row["mae_bps"]) if row["mae_bps"] is not None else None,
            signal_created_at=datetime.fromisoformat(row["signal_created_at"]),
            forward_completed_at=datetime.fromisoformat(row["forward_completed_at"]),
        )

    @staticmethod
    def _validate_completed_row(row: sqlite3.Row) -> None:
        if row["forward_result_id"] is None:
            raise ValueError("completed Forward job has no result")
        job_semantics = (
            row["signal_id"],
            row["forward_horizon"],
            row["job_market_source"],
            row["job_market_data_version"],
            row["job_price_basis"],
            row["job_granularity"],
            row["job_projection_version"],
            row["job_formula_version"],
        )
        result_semantics = (
            row["result_signal_id"],
            row["result_horizon"],
            row["result_market_source"],
            row["result_market_data_version"],
            row["result_price_basis"],
            row["result_granularity"],
            row["result_projection_version"],
            row["result_formula_version"],
        )
        if result_semantics != job_semantics:
            raise ValueError("completed Forward job and result semantics differ")

    @staticmethod
    def _validate_evaluations(
        snapshot: EvaluationInputSnapshot,
        evaluations: tuple[CohortEvaluation, ...],
    ) -> None:
        evaluated_inputs = tuple(
            sorted(
                input_id
                for evaluation in evaluations
                for input_id in evaluation.sample_input_ids
            )
        )
        if evaluated_inputs != snapshot.ordered_input_identity:
            raise ValueError("Evaluation reports do not cover the exact input snapshot")
        cohort_ids = tuple(item.cohort.cohort_id for item in evaluations)
        if len(set(cohort_ids)) != len(cohort_ids):
            raise ValueError("Evaluation Run contains duplicate cohort reports")

    @staticmethod
    def _report(row: sqlite3.Row) -> StoredEvaluationReport:
        return StoredEvaluationReport(
            report_id=row["report_id"],
            run_id=row["run_id"],
            cohort_id=row["cohort_id"],
            cohort_identity=_json_object(row["cohort_identity_json"]),
            metrics=_json_object(row["metrics_json"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )


def _status_exclusion(status: ForwardJobStatus) -> EvaluationExclusionReason:
    reasons = {
        ForwardJobStatus.PENDING: EvaluationExclusionReason.PENDING_FORWARD_JOB,
        ForwardJobStatus.FAILED: EvaluationExclusionReason.FAILED_FORWARD_JOB,
        ForwardJobStatus.UNAVAILABLE: EvaluationExclusionReason.UNAVAILABLE_FORWARD_JOB,
    }
    try:
        return reasons[status]
    except KeyError as error:
        raise ValueError("completed Forward job cannot be excluded") from error


def _supported_target(target_type: str, target_value: str) -> bool:
    return (target_type == "currency" and target_value in {"USD", "JPY"}) or (
        target_type == "pair" and target_value == "USD_JPY"
    )


def _report_id(run_id: str, cohort_id: str) -> str:
    return "evaluation-report-" + _digest((run_id, cohort_id))


def _digest(payload: object) -> str:
    return hashlib.sha256(_canonical_json(payload).encode()).hexdigest()


def _canonical_json(payload: object) -> str:
    return json.dumps(
        payload,
        default=_json_default,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def _json_default(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"unsupported evaluation JSON value: {type(value).__name__}")


def _json_object(value: str) -> Mapping[str, Any]:
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("persisted evaluation JSON must be an object")
    return parsed


_EVALUATION_INPUT_QUERY = """
SELECT
    signal.id AS signal_id,
    signal.target_type AS target_type,
    signal.target_value AS target_value,
    signal.signal_type AS signal_type,
    signal.direction AS signal_direction,
    signal.horizon AS signal_horizon,
    signal.created_at AS signal_created_at,
    signal.producer_version AS producer_version,
    signal.model_version AS model_version,
    signal.prompt_version AS prompt_version,
    signal.scorer_version AS scorer_version,
    signal.transformation_version AS transformation_version,
    job.job_id AS job_id,
    job.horizon AS forward_horizon,
    job.projection_version AS job_projection_version,
    job.market_source AS job_market_source,
    job.market_data_version AS job_market_data_version,
    job.price_basis AS job_price_basis,
    job.granularity AS job_granularity,
    job.formula_version AS job_formula_version,
    job.status AS job_status,
    result.result_id AS forward_result_id,
    result.signal_id AS result_signal_id,
    result.horizon AS result_horizon,
    result.target_return_bps AS target_return_bps,
    result.mfe_bps AS mfe_bps,
    result.mae_bps AS mae_bps,
    result.completed_at AS forward_completed_at,
    result.projection_version AS result_projection_version,
    result.market_source AS result_market_source,
    result.market_data_version AS result_market_data_version,
    result.price_basis AS result_price_basis,
    result.granularity AS result_granularity,
    result.formula_version AS result_formula_version
FROM research_forward_jobs AS job
JOIN signals AS signal ON signal.id = job.signal_id
LEFT JOIN research_forward_results AS result ON result.result_id = job.result_id
ORDER BY signal.created_at, signal.id, job.target_at, job.job_id
"""
