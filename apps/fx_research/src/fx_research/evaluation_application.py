import hashlib
import json
from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime

from .evaluation import (
    CohortEvaluation,
    EvaluationConfiguration,
    EvaluationExclusionReason,
    EvaluationInputSnapshot,
    ValidationAssessment,
    ValidationPolicy,
    ValidationStatus,
    group_samples,
)
from .evaluation_metrics import evaluate_cohort
from .evaluation_persistence import SQLiteEvaluationStore, StoredEvaluationReport


@dataclass(frozen=True, slots=True)
class EvaluateSignalsOnceResult:
    completed_forward_results_scanned: int
    evaluation_sample_count: int
    unsupported_signal_count: int
    incomplete_horizon_signal_count: int
    cohort_count: int
    reports_created: int
    reports_reused: int
    assessments_created: int
    undefined_metric_count: int
    insufficient_sample_count: int
    failed: int


class EvaluateSignalsOnceService:
    def __init__(
        self,
        store: SQLiteEvaluationStore,
        *,
        clock: Callable[[], datetime],
        configuration: EvaluationConfiguration | None = None,
    ) -> None:
        self._store = store
        self._clock = clock
        self._configuration = configuration or EvaluationConfiguration()

    def run(
        self, validation_policy: ValidationPolicy | None = None
    ) -> EvaluateSignalsOnceResult:
        snapshot = self._store.capture_inputs()
        exclusions_by_cohort: dict[str, list[EvaluationExclusionReason]] = defaultdict(
            list
        )
        for excluded in snapshot.exclusions:
            exclusions_by_cohort[excluded.cohort.cohort_id].append(excluded.reason)
        evaluations = []
        failed = 0
        for cohort, samples in group_samples(snapshot.samples):
            try:
                evaluations.append(
                    evaluate_cohort(
                        samples,
                        tuple(exclusions_by_cohort[cohort.cohort_id]),
                        metric_configuration=self._configuration.metric,
                        bootstrap_configuration=self._configuration.bootstrap,
                    )
                )
            except (ArithmeticError, ValueError):
                failed += 1
        if failed:
            return self._summary(snapshot, evaluations, failed)
        created_at = self._clock()
        appended = self._store.append_run(
            snapshot,
            tuple(evaluations),
            self._configuration,
            created_at=created_at,
        )
        assessments_created = 0
        if validation_policy is not None:
            self._store.append_policy(validation_policy, created_at=created_at)
            reports_by_cohort = {
                report.cohort_id: report for report in appended.run.reports
            }
            for evaluation in evaluations:
                report = reports_by_cohort[evaluation.cohort.cohort_id]
                assessment = assess_evaluation(
                    appended.run.run_id,
                    report,
                    evaluation,
                    validation_policy,
                    created_at=self._clock(),
                )
                assessments_created += int(
                    self._store.append_assessment(assessment, evaluation)
                )
        undefined = sum(item.metrics.undefined_metric_count for item in evaluations)
        insufficient = sum(
            item.metrics.insufficient_sample_count for item in evaluations
        )
        report_count = len(appended.run.reports)
        return EvaluateSignalsOnceResult(
            completed_forward_results_scanned=snapshot.completed_results_scanned,
            evaluation_sample_count=len(snapshot.samples),
            unsupported_signal_count=len(snapshot.unsupported_signal_ids),
            incomplete_horizon_signal_count=len(
                snapshot.incomplete_horizon_signal_ids
            ),
            cohort_count=len(evaluations),
            reports_created=report_count if appended.created else 0,
            reports_reused=0 if appended.created else report_count,
            assessments_created=assessments_created,
            undefined_metric_count=undefined,
            insufficient_sample_count=insufficient,
            failed=0,
        )

    @staticmethod
    def _summary(
        snapshot: EvaluationInputSnapshot,
        evaluations: list[CohortEvaluation],
        failed: int,
    ) -> EvaluateSignalsOnceResult:
        return EvaluateSignalsOnceResult(
            completed_forward_results_scanned=snapshot.completed_results_scanned,
            evaluation_sample_count=sum(
                len(item.sample_input_ids) for item in evaluations
            ),
            unsupported_signal_count=len(snapshot.unsupported_signal_ids),
            incomplete_horizon_signal_count=len(
                snapshot.incomplete_horizon_signal_ids
            ),
            cohort_count=len(evaluations) + failed,
            reports_created=0,
            reports_reused=0,
            assessments_created=0,
            undefined_metric_count=sum(
                item.metrics.undefined_metric_count for item in evaluations
            ),
            insufficient_sample_count=sum(
                item.metrics.insufficient_sample_count for item in evaluations
            ),
            failed=failed,
        )


def assess_evaluation(
    run_id: str,
    report: StoredEvaluationReport,
    evaluation: CohortEvaluation,
    policy: ValidationPolicy,
    *,
    created_at: datetime,
) -> ValidationAssessment:
    metrics = evaluation.metrics
    conditions = (
        (
            "minimum_sample_count",
            metrics.diagnostics.included_samples >= policy.minimum_sample_count,
        ),
        (
            "minimum_spearman",
            metrics.spearman.value is not None
            and metrics.spearman.value >= policy.minimum_spearman,
        ),
        (
            "minimum_spearman_ci_lower",
            policy.minimum_spearman_ci_lower is None
            or (
                metrics.spearman.confidence_lower is not None
                and metrics.spearman.confidence_lower
                >= policy.minimum_spearman_ci_lower
            ),
        ),
        (
            "minimum_hit_rate",
            metrics.hit_rate.value is not None
            and metrics.hit_rate.value >= policy.minimum_hit_rate,
        ),
        (
            "minimum_hit_rate_ci_lower",
            policy.minimum_hit_rate_ci_lower is None
            or (
                metrics.hit_rate.confidence_lower is not None
                and metrics.hit_rate.confidence_lower
                >= policy.minimum_hit_rate_ci_lower
            ),
        ),
        (
            "required_non_empty_bucket_count",
            metrics.monotonicity.non_empty_bucket_count
            >= policy.required_non_empty_bucket_count,
        ),
        (
            "minimum_adjacent_step_ratio",
            metrics.monotonicity.adjacent_step_ratio is not None
            and metrics.monotonicity.adjacent_step_ratio
            >= policy.minimum_adjacent_step_ratio,
        ),
        (
            "stability_slice_minimum_count",
            bool(metrics.stability_slices)
            and all(
                item.sample_count >= policy.stability_slice_minimum_count
                for item in metrics.stability_slices
            ),
        ),
    )
    condition_map = dict(conditions)
    if all(condition_map.values()):
        status = ValidationStatus.VALIDATED_FOR_RESEARCH
    elif all(
        condition_map[name]
        for name in (
            "minimum_sample_count",
            "minimum_spearman",
            "minimum_hit_rate",
        )
    ):
        status = ValidationStatus.PROMISING
    else:
        status = ValidationStatus.EXPERIMENTAL
    assessment_id = "validation-assessment-" + hashlib.sha256(
        json.dumps(
            (run_id, report.report_id, policy.policy_version, policy.content_hash),
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return ValidationAssessment(
        assessment_id=assessment_id,
        evaluation_run_id=run_id,
        report_id=report.report_id,
        policy_version=policy.policy_version,
        policy_content_hash=policy.content_hash,
        status=status,
        condition_results=conditions,
        created_at=created_at,
    )


def validation_policy_from_mapping(payload: Mapping[str, object]) -> ValidationPolicy:
    required = {
        "policy_version",
        "minimum_sample_count",
        "minimum_spearman",
        "minimum_spearman_ci_lower",
        "minimum_hit_rate",
        "minimum_hit_rate_ci_lower",
        "required_non_empty_bucket_count",
        "minimum_adjacent_step_ratio",
        "stability_slice_minimum_count",
    }
    if set(payload) != required:
        raise ValueError("validation policy fields do not match the required contract")
    try:
        return ValidationPolicy(
            policy_version=_text(payload["policy_version"]),
            minimum_sample_count=_integer(payload["minimum_sample_count"]),
            minimum_spearman=_number(payload["minimum_spearman"]),
            minimum_spearman_ci_lower=_optional_number(
                payload["minimum_spearman_ci_lower"]
            ),
            minimum_hit_rate=_number(payload["minimum_hit_rate"]),
            minimum_hit_rate_ci_lower=_optional_number(
                payload["minimum_hit_rate_ci_lower"]
            ),
            required_non_empty_bucket_count=_integer(
                payload["required_non_empty_bucket_count"]
            ),
            minimum_adjacent_step_ratio=_number(
                payload["minimum_adjacent_step_ratio"]
            ),
            stability_slice_minimum_count=_integer(
                payload["stability_slice_minimum_count"]
            ),
        )
    except (TypeError, ValueError) as error:
        raise ValueError("validation policy contains invalid values") from error


def _text(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("value must be text")
    return value


def _integer(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError("value must be an integer")
    return value


def _number(value: object) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise TypeError("value must be numeric")
    return float(value)


def _optional_number(value: object) -> float | None:
    return None if value is None else _number(value)
