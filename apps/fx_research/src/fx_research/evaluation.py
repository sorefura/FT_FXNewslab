import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from fx_core import CurrencyTarget, Horizon, PairTarget, Signal, SignalId
from fx_core.time import require_utc

from .forward import ForwardJobStatus, ForwardObservationJob, ForwardResult

EVALUATOR_VERSION = "signal-validation-v1"
SCORE_DEFINITION_VERSION = "signal-direction-v1"
COHORT_DEFINITION_VERSION = "strict-evaluation-cohort-v1"
METRIC_CONFIGURATION_VERSION = "research-metrics-v1"
BOOTSTRAP_VERSION = "paired-bootstrap-v1"
DEFAULT_BOOTSTRAP_SEED = 20260714
DEFAULT_BOOTSTRAP_ITERATIONS = 2_000
SCORE_BUCKET_BOUNDARIES = (-1.0, -0.6, -0.2, 0.2, 0.6, 1.0)


class UndefinedReason(StrEnum):
    INSUFFICIENT_SAMPLE = "INSUFFICIENT_SAMPLE"
    CONSTANT_SCORE = "CONSTANT_SCORE"
    CONSTANT_RETURN = "CONSTANT_RETURN"
    NO_ELIGIBLE_SAMPLE = "NO_ELIGIBLE_SAMPLE"
    NO_VALID_BOOTSTRAP_SAMPLE = "NO_VALID_BOOTSTRAP_SAMPLE"


class EvaluationExclusionReason(StrEnum):
    PENDING_FORWARD_JOB = "PENDING_FORWARD_JOB"
    FAILED_FORWARD_JOB = "FAILED_FORWARD_JOB"
    UNAVAILABLE_FORWARD_JOB = "UNAVAILABLE_FORWARD_JOB"
    INCOMPLETE_HORIZON = "INCOMPLETE_HORIZON"
    UNSUPPORTED_TARGET = "UNSUPPORTED_TARGET"


class ValidationStatus(StrEnum):
    EXPERIMENTAL = "EXPERIMENTAL"
    PROMISING = "PROMISING"
    VALIDATED_FOR_RESEARCH = "VALIDATED_FOR_RESEARCH"


@dataclass(frozen=True, slots=True)
class BootstrapConfiguration:
    version: str = BOOTSTRAP_VERSION
    seed: int = DEFAULT_BOOTSTRAP_SEED
    iterations: int = DEFAULT_BOOTSTRAP_ITERATIONS
    confidence_level: float = 0.95

    def __post_init__(self) -> None:
        _require_text(self.version, "bootstrap version")
        if self.iterations <= 0:
            raise ValueError("bootstrap iterations must be positive")
        if not 0 < self.confidence_level < 1:
            raise ValueError("bootstrap confidence level must be between zero and one")

    def identity_payload(self) -> dict[str, object]:
        return {
            "version": self.version,
            "seed": self.seed,
            "iterations": self.iterations,
            "confidence_level": self.confidence_level,
        }


@dataclass(frozen=True, slots=True)
class MetricConfiguration:
    version: str = METRIC_CONFIGURATION_VERSION
    bucket_boundaries: tuple[float, ...] = SCORE_BUCKET_BOUNDARIES
    lower_quantile: float = 0.25
    upper_quantile: float = 0.75

    def __post_init__(self) -> None:
        _require_text(self.version, "metric configuration version")
        if self.bucket_boundaries != tuple(sorted(set(self.bucket_boundaries))):
            raise ValueError("score bucket boundaries must be unique and ordered")
        if len(self.bucket_boundaries) < 2:
            raise ValueError("at least two score bucket boundaries are required")
        if not 0 <= self.lower_quantile < self.upper_quantile <= 1:
            raise ValueError("metric quantiles must be ordered within [0, 1]")

    def identity_payload(self) -> dict[str, object]:
        return {
            "version": self.version,
            "bucket_boundaries": self.bucket_boundaries,
            "lower_quantile": self.lower_quantile,
            "upper_quantile": self.upper_quantile,
        }


@dataclass(frozen=True, slots=True)
class EvaluationConfiguration:
    evaluator_version: str = EVALUATOR_VERSION
    score_definition_version: str = SCORE_DEFINITION_VERSION
    cohort_definition_version: str = COHORT_DEFINITION_VERSION
    metric: MetricConfiguration = field(default_factory=MetricConfiguration)
    bootstrap: BootstrapConfiguration = field(default_factory=BootstrapConfiguration)

    def __post_init__(self) -> None:
        for value, label in (
            (self.evaluator_version, "evaluator version"),
            (self.score_definition_version, "score definition version"),
            (self.cohort_definition_version, "cohort definition version"),
        ):
            _require_text(value, label)
        if self.score_definition_version != SCORE_DEFINITION_VERSION:
            raise ValueError("unsupported score definition version")

    def identity_payload(self) -> dict[str, object]:
        return {
            "evaluator_version": self.evaluator_version,
            "score_definition_version": self.score_definition_version,
            "cohort_definition_version": self.cohort_definition_version,
            "metric": self.metric.identity_payload(),
            "bootstrap": self.bootstrap.identity_payload(),
        }


@dataclass(frozen=True, slots=True)
class CohortIdentity:
    signal_type: str
    target_type: str
    target_value: str
    signal_horizon: Horizon
    forward_horizon: Horizon
    producer_version: str | None
    model_version: str | None
    prompt_version: str | None
    scorer_version: str
    transformation_version: str | None
    market_source: str
    market_data_version: str
    price_basis: str
    granularity: str
    projection_version: str
    formula_version: str
    score_definition_version: str = SCORE_DEFINITION_VERSION

    def __post_init__(self) -> None:
        for value, label in (
            (self.signal_type, "cohort signal type"),
            (self.target_type, "cohort target type"),
            (self.target_value, "cohort target value"),
            (self.scorer_version, "cohort scorer version"),
            (self.market_source, "cohort market source"),
            (self.market_data_version, "cohort market data version"),
            (self.price_basis, "cohort price basis"),
            (self.granularity, "cohort granularity"),
            (self.projection_version, "cohort projection version"),
            (self.formula_version, "cohort formula version"),
            (self.score_definition_version, "cohort score definition version"),
        ):
            _require_text(value, label)
        for optional_value, label in (
            (self.producer_version, "cohort producer version"),
            (self.model_version, "cohort model version"),
            (self.prompt_version, "cohort prompt version"),
            (self.transformation_version, "cohort transformation version"),
        ):
            _optional_text(optional_value, label)
        if self.target_type not in {"currency", "pair"}:
            raise ValueError("cohort target type must be currency or pair")

    @property
    def cohort_id(self) -> str:
        return "evaluation-cohort-" + _digest(self.identity_payload())

    def identity_payload(self) -> dict[str, object]:
        return {
            "signal_type": self.signal_type,
            "target_type": self.target_type,
            "target_value": self.target_value,
            "signal_horizon": self.signal_horizon.value,
            "forward_horizon": self.forward_horizon.value,
            "producer_version": self.producer_version,
            "model_version": self.model_version,
            "prompt_version": self.prompt_version,
            "scorer_version": self.scorer_version,
            "transformation_version": self.transformation_version,
            "market_source": self.market_source,
            "market_data_version": self.market_data_version,
            "price_basis": self.price_basis,
            "granularity": self.granularity,
            "projection_version": self.projection_version,
            "formula_version": self.formula_version,
            "score_definition_version": self.score_definition_version,
        }


@dataclass(frozen=True, slots=True)
class EvaluationSample:
    signal_id: SignalId
    forward_result_id: str
    cohort: CohortIdentity
    score: float
    target_return_bps: Decimal
    mfe_bps: Decimal | None
    mae_bps: Decimal | None
    signal_created_at: datetime
    forward_completed_at: datetime

    def __post_init__(self) -> None:
        _require_text(self.forward_result_id, "ForwardResult id")
        if not math.isfinite(self.score):
            raise ValueError("evaluation score must be finite")
        for value, label in (
            (self.target_return_bps, "target return"),
            (self.mfe_bps, "MFE"),
            (self.mae_bps, "MAE"),
        ):
            if value is not None and not value.is_finite():
                raise ValueError(f"{label} must be finite")
        require_utc(self.signal_created_at, "evaluation Signal created_at")
        require_utc(self.forward_completed_at, "evaluation ForwardResult completed_at")

    @property
    def input_identity(self) -> tuple[str, str]:
        return self.signal_id.value, self.forward_result_id


@dataclass(frozen=True, slots=True)
class ExcludedForwardObservation:
    signal_id: SignalId
    job_id: str
    cohort: CohortIdentity
    reason: EvaluationExclusionReason

    def __post_init__(self) -> None:
        _require_text(self.job_id, "excluded Forward job id")


@dataclass(frozen=True, slots=True)
class EvaluationInputSnapshot:
    signals_scanned: int
    completed_results_scanned: int
    samples: tuple[EvaluationSample, ...]
    exclusions: tuple[ExcludedForwardObservation, ...]
    unsupported_signal_ids: tuple[SignalId, ...] = ()
    incomplete_horizon_signal_ids: tuple[SignalId, ...] = ()

    def __post_init__(self) -> None:
        if self.signals_scanned < 0 or self.completed_results_scanned < 0:
            raise ValueError("evaluation input counts must not be negative")
        if self.completed_results_scanned != len(self.samples):
            raise ValueError("completed result count must match Evaluation samples")
        if tuple(sorted(self.samples, key=lambda item: item.input_identity)) != self.samples:
            raise ValueError("Evaluation samples must use deterministic input order")

    @property
    def ordered_input_identity(self) -> tuple[tuple[str, str], ...]:
        return tuple(item.input_identity for item in self.samples)


@dataclass(frozen=True, slots=True)
class SpearmanMetric:
    sample_count: int
    value: float | None
    confidence_lower: float | None
    confidence_upper: float | None
    undefined_reason: UndefinedReason | None
    bootstrap_valid_iterations: int


@dataclass(frozen=True, slots=True)
class HitRateMetric:
    total_sample_count: int
    eligible_sample_count: int
    hit_count: int
    neutral_signal_count: int
    zero_return_count: int
    value: float | None
    confidence_lower: float | None
    confidence_upper: float | None
    undefined_reason: UndefinedReason | None


@dataclass(frozen=True, slots=True)
class ScoreBucketMetric:
    ordinal: int
    lower: float
    upper: float
    includes_upper: bool
    sample_count: int
    mean_target_return_bps: Decimal | None
    median_target_return_bps: Decimal | None
    confidence_lower_bps: Decimal | None
    confidence_upper_bps: Decimal | None
    empty: bool


@dataclass(frozen=True, slots=True)
class MonotonicityMetric:
    non_empty_bucket_count: int
    adjacent_non_decreasing_step_count: int
    adjacent_step_ratio: float | None
    bucket_mean_spearman: float | None
    unbucketed_sample_count: int


@dataclass(frozen=True, slots=True)
class ExcursionMetric:
    eligible_count: int
    null_count: int
    mean_bps: Decimal | None
    median_bps: Decimal | None
    lower_quantile_bps: Decimal | None
    upper_quantile_bps: Decimal | None


@dataclass(frozen=True, slots=True)
class StabilitySlice:
    period: str
    sample_count: int
    spearman_value: float | None
    undefined_reason: UndefinedReason | None


@dataclass(frozen=True, slots=True)
class SampleDiagnostics:
    total_samples: int
    included_samples: int
    excluded_samples: int
    exclusion_reason_counts: tuple[tuple[EvaluationExclusionReason, int], ...]
    first_signal_created_at: datetime | None
    last_signal_created_at: datetime | None
    first_forward_completed_at: datetime | None
    last_forward_completed_at: datetime | None


@dataclass(frozen=True, slots=True)
class EvaluationMetrics:
    spearman: SpearmanMetric
    hit_rate: HitRateMetric
    buckets: tuple[ScoreBucketMetric, ...]
    monotonicity: MonotonicityMetric
    mfe: ExcursionMetric
    mae: ExcursionMetric
    stability_slices: tuple[StabilitySlice, ...]
    diagnostics: SampleDiagnostics

    @property
    def undefined_metric_count(self) -> int:
        count = int(self.spearman.undefined_reason is not None)
        count += int(self.hit_rate.undefined_reason is not None)
        return count + sum(
            item.undefined_reason is not None for item in self.stability_slices
        )

    @property
    def insufficient_sample_count(self) -> int:
        return int(self.spearman.undefined_reason is UndefinedReason.INSUFFICIENT_SAMPLE) + sum(
            item.undefined_reason is UndefinedReason.INSUFFICIENT_SAMPLE
            for item in self.stability_slices
        )


@dataclass(frozen=True, slots=True)
class CohortEvaluation:
    cohort: CohortIdentity
    sample_input_ids: tuple[tuple[str, str], ...]
    metrics: EvaluationMetrics


@dataclass(frozen=True, slots=True)
class ValidationPolicy:
    policy_version: str
    minimum_sample_count: int
    minimum_spearman: float
    minimum_spearman_ci_lower: float | None
    minimum_hit_rate: float
    minimum_hit_rate_ci_lower: float | None
    required_non_empty_bucket_count: int
    minimum_adjacent_step_ratio: float
    stability_slice_minimum_count: int

    def __post_init__(self) -> None:
        _require_text(self.policy_version, "validation policy version")
        if self.minimum_sample_count < 1 or self.stability_slice_minimum_count < 1:
            raise ValueError("validation sample minimums must be positive")
        if self.required_non_empty_bucket_count < 1:
            raise ValueError("required non-empty bucket count must be positive")
        for value, label in (
            (self.minimum_hit_rate, "minimum hit rate"),
            (self.minimum_adjacent_step_ratio, "minimum adjacent step ratio"),
        ):
            if not 0 <= value <= 1:
                raise ValueError(f"{label} must be within [0, 1]")
        if self.minimum_hit_rate_ci_lower is not None and not (
            0 <= self.minimum_hit_rate_ci_lower <= 1
        ):
            raise ValueError("minimum Hit Rate CI lower bound must be within [0, 1]")
        for optional_value, label in (
            (self.minimum_spearman, "minimum Spearman"),
            (self.minimum_spearman_ci_lower, "minimum Spearman CI lower bound"),
        ):
            if optional_value is not None and not -1 <= optional_value <= 1:
                raise ValueError(f"{label} must be within [-1, 1]")

    def identity_payload(self) -> dict[str, object]:
        return {
            "policy_version": self.policy_version,
            "minimum_sample_count": self.minimum_sample_count,
            "minimum_spearman": self.minimum_spearman,
            "minimum_spearman_ci_lower": self.minimum_spearman_ci_lower,
            "minimum_hit_rate": self.minimum_hit_rate,
            "minimum_hit_rate_ci_lower": self.minimum_hit_rate_ci_lower,
            "required_non_empty_bucket_count": self.required_non_empty_bucket_count,
            "minimum_adjacent_step_ratio": self.minimum_adjacent_step_ratio,
            "stability_slice_minimum_count": self.stability_slice_minimum_count,
        }

    @property
    def content_hash(self) -> str:
        return _digest(self.identity_payload())


@dataclass(frozen=True, slots=True)
class ValidationAssessment:
    assessment_id: str
    evaluation_run_id: str
    report_id: str
    policy_version: str
    policy_content_hash: str
    status: ValidationStatus
    condition_results: tuple[tuple[str, bool], ...]
    created_at: datetime

    def __post_init__(self) -> None:
        for value, label in (
            (self.assessment_id, "assessment id"),
            (self.evaluation_run_id, "assessment Evaluation Run id"),
            (self.report_id, "assessment report id"),
            (self.policy_version, "assessment policy version"),
            (self.policy_content_hash, "assessment policy content hash"),
        ):
            _require_text(value, label)
        require_utc(self.created_at, "assessment created_at")


def evaluation_sample(signal: Signal, result: ForwardResult) -> EvaluationSample:
    if result.signal_id != signal.signal_id:
        raise ValueError("ForwardResult does not belong to the supplied Signal")
    return EvaluationSample(
        signal_id=signal.signal_id,
        forward_result_id=result.result_id,
        cohort=cohort_identity(signal, result),
        score=float(signal.direction.value),
        target_return_bps=result.target_return_bps,
        mfe_bps=result.mfe_bps,
        mae_bps=result.mae_bps,
        signal_created_at=signal.created_at,
        forward_completed_at=result.completed_at,
    )


def cohort_identity(
    signal: Signal,
    forward: ForwardResult | ForwardObservationJob,
) -> CohortIdentity:
    target_type, target_value = _target_identity(signal)
    if isinstance(forward, ForwardResult):
        forward_horizon = forward.horizon
        market_source = forward.market_source
        market_data_version = forward.market_data_version
        price_basis = forward.price_basis
        granularity = forward.granularity
        projection_version = forward.projection_version
        formula_version = forward.formula_version
    else:
        forward_horizon = forward.horizon
        market_source = forward.market_source
        market_data_version = forward.market_data_version
        price_basis = forward.price_basis
        granularity = forward.granularity
        projection_version = forward.projection.version
        formula_version = forward.formula_version
    scorer_version = signal.versions.scorer_version
    if scorer_version is None:
        raise ValueError("Signal scorer version is required for evaluation")
    return CohortIdentity(
        signal_type=signal.signal_type,
        target_type=target_type,
        target_value=target_value,
        signal_horizon=signal.horizon,
        forward_horizon=forward_horizon,
        producer_version=signal.versions.producer_version,
        model_version=signal.versions.model_version,
        prompt_version=signal.versions.prompt_version,
        scorer_version=scorer_version,
        transformation_version=signal.versions.transformation_version,
        market_source=market_source,
        market_data_version=market_data_version,
        price_basis=price_basis,
        granularity=granularity,
        projection_version=projection_version,
        formula_version=formula_version,
    )


def exclusion_reason(status: ForwardJobStatus) -> EvaluationExclusionReason:
    mapping = {
        ForwardJobStatus.PENDING: EvaluationExclusionReason.PENDING_FORWARD_JOB,
        ForwardJobStatus.FAILED: EvaluationExclusionReason.FAILED_FORWARD_JOB,
        ForwardJobStatus.UNAVAILABLE: EvaluationExclusionReason.UNAVAILABLE_FORWARD_JOB,
    }
    try:
        return mapping[status]
    except KeyError as error:
        raise ValueError("completed Forward job is not an exclusion") from error


def group_samples(
    samples: tuple[EvaluationSample, ...],
) -> tuple[tuple[CohortIdentity, tuple[EvaluationSample, ...]], ...]:
    grouped: dict[str, tuple[CohortIdentity, list[EvaluationSample]]] = {}
    for sample in samples:
        entry = grouped.setdefault(sample.cohort.cohort_id, (sample.cohort, []))
        if entry[0] != sample.cohort:
            raise ValueError("cohort identity collision")
        entry[1].append(sample)
    return tuple(
        (
            cohort,
            tuple(sorted(items, key=lambda item: item.input_identity)),
        )
        for _, (cohort, items) in sorted(grouped.items())
    )


def _target_identity(signal: Signal) -> tuple[str, str]:
    if isinstance(signal.target, CurrencyTarget):
        return "currency", signal.target.currency.code
    if isinstance(signal.target, PairTarget):
        return "pair", signal.target.pair.symbol
    raise TypeError("unsupported Signal target")


def _require_text(value: str, label: str) -> None:
    if not value.strip():
        raise ValueError(f"{label} must not be blank")


def _optional_text(value: str | None, label: str) -> None:
    if value is not None:
        _require_text(value, label)


def _digest(payload: object) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()
