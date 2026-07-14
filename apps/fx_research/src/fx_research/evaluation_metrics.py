import math
import random
from collections import Counter
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from statistics import median
from typing import TypeVar, cast, overload

from .evaluation import (
    BootstrapConfiguration,
    CohortEvaluation,
    EvaluationExclusionReason,
    EvaluationMetrics,
    EvaluationSample,
    ExcursionMetric,
    HitRateMetric,
    MetricConfiguration,
    MonotonicityMetric,
    SampleDiagnostics,
    ScoreBucketMetric,
    SpearmanMetric,
    StabilitySlice,
    UndefinedReason,
)

_T = TypeVar("_T", float, Decimal)
_WILSON_Z_95 = 1.959963984540054


def evaluate_cohort(
    samples: tuple[EvaluationSample, ...],
    exclusions: tuple[EvaluationExclusionReason, ...],
    *,
    metric_configuration: MetricConfiguration,
    bootstrap_configuration: BootstrapConfiguration,
) -> CohortEvaluation:
    if not samples:
        raise ValueError("cohort evaluation requires at least one sample")
    cohort = samples[0].cohort
    if any(item.cohort != cohort for item in samples):
        raise ValueError("cohort evaluation cannot combine different identities")
    scores = tuple(item.score for item in samples)
    returns = tuple(item.target_return_bps for item in samples)
    buckets, monotonicity = score_bucket_metrics(
        scores,
        returns,
        metric_configuration=metric_configuration,
        bootstrap_configuration=bootstrap_configuration,
    )
    metrics = EvaluationMetrics(
        spearman=spearman_ic(scores, returns, bootstrap_configuration),
        hit_rate=hit_rate(scores, returns),
        buckets=buckets,
        monotonicity=monotonicity,
        mfe=excursion_summary(
            tuple(item.mfe_bps for item in samples), metric_configuration
        ),
        mae=excursion_summary(
            tuple(item.mae_bps for item in samples), metric_configuration
        ),
        stability_slices=quarterly_stability(samples),
        diagnostics=sample_diagnostics(samples, exclusions),
    )
    return CohortEvaluation(
        cohort=cohort,
        sample_input_ids=tuple(item.input_identity for item in samples),
        metrics=metrics,
    )


def spearman_ic(
    scores: Sequence[float],
    returns: Sequence[Decimal],
    bootstrap: BootstrapConfiguration | None = None,
) -> SpearmanMetric:
    if len(scores) != len(returns):
        raise ValueError("Spearman inputs must have equal length")
    value, reason = _spearman_value(scores, returns)
    if value is None:
        return SpearmanMetric(len(scores), None, None, None, reason, 0)
    if bootstrap is None:
        return SpearmanMetric(len(scores), value, None, None, None, 0)
    randomizer = random.Random(bootstrap.seed)
    bootstrapped: list[float] = []
    for _ in range(bootstrap.iterations):
        indices = tuple(randomizer.randrange(len(scores)) for _ in scores)
        sample_scores = tuple(scores[index] for index in indices)
        sample_returns = tuple(returns[index] for index in indices)
        sampled, _ = _spearman_value(sample_scores, sample_returns)
        if sampled is not None:
            bootstrapped.append(sampled)
    if not bootstrapped:
        return SpearmanMetric(
            len(scores),
            value,
            None,
            None,
            UndefinedReason.NO_VALID_BOOTSTRAP_SAMPLE,
            0,
        )
    tail = (1 - bootstrap.confidence_level) / 2
    return SpearmanMetric(
        len(scores),
        value,
        _percentile(tuple(bootstrapped), tail),
        _percentile(tuple(bootstrapped), 1 - tail),
        None,
        len(bootstrapped),
    )


def hit_rate(scores: Sequence[float], returns: Sequence[Decimal]) -> HitRateMetric:
    if len(scores) != len(returns):
        raise ValueError("Hit Rate inputs must have equal length")
    neutral = sum(score == 0 for score in scores)
    zero_returns = sum(value == 0 for value in returns)
    eligible = tuple(
        (score, value)
        for score, value in zip(scores, returns, strict=True)
        if score != 0 and value != 0
    )
    hits = sum((score > 0) == (value > 0) for score, value in eligible)
    if not eligible:
        return HitRateMetric(
            total_sample_count=len(scores),
            eligible_sample_count=0,
            hit_count=0,
            neutral_signal_count=neutral,
            zero_return_count=zero_returns,
            value=None,
            confidence_lower=None,
            confidence_upper=None,
            undefined_reason=UndefinedReason.NO_ELIGIBLE_SAMPLE,
        )
    value = hits / len(eligible)
    lower, upper = _wilson_interval(hits, len(eligible))
    return HitRateMetric(
        total_sample_count=len(scores),
        eligible_sample_count=len(eligible),
        hit_count=hits,
        neutral_signal_count=neutral,
        zero_return_count=zero_returns,
        value=value,
        confidence_lower=lower,
        confidence_upper=upper,
        undefined_reason=None,
    )


def score_bucket_metrics(
    scores: Sequence[float],
    returns: Sequence[Decimal],
    *,
    metric_configuration: MetricConfiguration,
    bootstrap_configuration: BootstrapConfiguration,
) -> tuple[tuple[ScoreBucketMetric, ...], MonotonicityMetric]:
    if len(scores) != len(returns):
        raise ValueError("bucket inputs must have equal length")
    boundaries = metric_configuration.bucket_boundaries
    bucket_values: list[list[Decimal]] = [
        [] for _ in range(len(boundaries) - 1)
    ]
    unbucketed = 0
    for score, value in zip(scores, returns, strict=True):
        ordinal = _bucket_ordinal(score, boundaries)
        if ordinal is None:
            unbucketed += 1
        else:
            bucket_values[ordinal].append(value)
    buckets = tuple(
        _bucket_metric(
            ordinal,
            boundaries[ordinal],
            boundaries[ordinal + 1],
            values,
            includes_upper=ordinal == len(bucket_values) - 1,
            bootstrap=bootstrap_configuration,
        )
        for ordinal, values in enumerate(bucket_values)
    )
    non_empty = tuple(item for item in buckets if not item.empty)
    means = tuple(
        item.mean_target_return_bps
        for item in non_empty
        if item.mean_target_return_bps is not None
    )
    step_count = sum(
        current <= following
        for current, following in zip(means[:-1], means[1:], strict=True)
    )
    step_ratio = step_count / (len(means) - 1) if len(means) >= 2 else None
    order = tuple(float(index) for index in range(len(means)))
    bucket_spearman, _ = _spearman_value(order, means)
    return buckets, MonotonicityMetric(
        non_empty_bucket_count=len(non_empty),
        adjacent_non_decreasing_step_count=step_count,
        adjacent_step_ratio=step_ratio,
        bucket_mean_spearman=bucket_spearman,
        unbucketed_sample_count=unbucketed,
    )


def excursion_summary(
    values: Sequence[Decimal | None], configuration: MetricConfiguration
) -> ExcursionMetric:
    eligible = tuple(item for item in values if item is not None)
    if not eligible:
        return ExcursionMetric(0, len(values), None, None, None, None)
    return ExcursionMetric(
        eligible_count=len(eligible),
        null_count=len(values) - len(eligible),
        mean_bps=sum(eligible, Decimal(0)) / len(eligible),
        median_bps=Decimal(median(eligible)),
        lower_quantile_bps=_percentile(eligible, configuration.lower_quantile),
        upper_quantile_bps=_percentile(eligible, configuration.upper_quantile),
    )


def quarterly_stability(
    samples: Sequence[EvaluationSample],
) -> tuple[StabilitySlice, ...]:
    grouped: dict[str, list[EvaluationSample]] = {}
    for sample in samples:
        grouped.setdefault(_quarter(sample.signal_created_at), []).append(sample)
    slices = []
    for period, period_samples in sorted(grouped.items()):
        metric = spearman_ic(
            tuple(item.score for item in period_samples),
            tuple(item.target_return_bps for item in period_samples),
        )
        slices.append(
            StabilitySlice(
                period=period,
                sample_count=len(period_samples),
                spearman_value=metric.value,
                undefined_reason=metric.undefined_reason,
            )
        )
    return tuple(slices)


def sample_diagnostics(
    samples: Sequence[EvaluationSample],
    exclusions: Sequence[EvaluationExclusionReason],
) -> SampleDiagnostics:
    reason_counts = Counter(exclusions)
    signal_times = tuple(item.signal_created_at for item in samples)
    result_times = tuple(item.forward_completed_at for item in samples)
    return SampleDiagnostics(
        total_samples=len(samples) + len(exclusions),
        included_samples=len(samples),
        excluded_samples=len(exclusions),
        exclusion_reason_counts=tuple(
            sorted(reason_counts.items(), key=lambda item: item[0].value)
        ),
        first_signal_created_at=min(signal_times) if signal_times else None,
        last_signal_created_at=max(signal_times) if signal_times else None,
        first_forward_completed_at=min(result_times) if result_times else None,
        last_forward_completed_at=max(result_times) if result_times else None,
    )


def _spearman_value(
    scores: Sequence[float], returns: Sequence[_T]
) -> tuple[float | None, UndefinedReason | None]:
    if len(scores) < 3:
        return None, UndefinedReason.INSUFFICIENT_SAMPLE
    if len(set(scores)) == 1:
        return None, UndefinedReason.CONSTANT_SCORE
    if len(set(returns)) == 1:
        return None, UndefinedReason.CONSTANT_RETURN
    score_ranks = _average_ranks(scores)
    return_ranks = _average_ranks(returns)
    mean_score = sum(score_ranks) / len(score_ranks)
    mean_return = sum(return_ranks) / len(return_ranks)
    numerator = sum(
        (score - mean_score) * (result - mean_return)
        for score, result in zip(score_ranks, return_ranks, strict=True)
    )
    score_variance = sum((value - mean_score) ** 2 for value in score_ranks)
    return_variance = sum((value - mean_return) ** 2 for value in return_ranks)
    return numerator / math.sqrt(score_variance * return_variance), None


def _average_ranks(values: Sequence[_T]) -> tuple[float, ...]:
    ordered = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [0.0] * len(values)
    start = 0
    while start < len(ordered):
        end = start + 1
        while end < len(ordered) and ordered[end][1] == ordered[start][1]:
            end += 1
        average_rank = ((start + 1) + end) / 2
        for position in range(start, end):
            ranks[ordered[position][0]] = average_rank
        start = end
    return tuple(ranks)


def _bucket_ordinal(score: float, boundaries: tuple[float, ...]) -> int | None:
    if score < boundaries[0] or score > boundaries[-1]:
        return None
    for ordinal, (lower, upper) in enumerate(
        zip(boundaries[:-1], boundaries[1:], strict=True)
    ):
        if lower <= score < upper or (
            ordinal == len(boundaries) - 2 and score == upper
        ):
            return ordinal
    return None


def _bucket_metric(
    ordinal: int,
    lower: float,
    upper: float,
    values: Sequence[Decimal],
    *,
    includes_upper: bool,
    bootstrap: BootstrapConfiguration,
) -> ScoreBucketMetric:
    if not values:
        return ScoreBucketMetric(
            ordinal, lower, upper, includes_upper, 0, None, None, None, None, True
        )
    value_tuple = tuple(values)
    mean = sum(value_tuple, Decimal(0)) / len(value_tuple)
    lower_interval, upper_interval = _bootstrap_mean_interval(
        value_tuple,
        bootstrap,
        seed_offset=ordinal + 1,
    )
    return ScoreBucketMetric(
        ordinal=ordinal,
        lower=lower,
        upper=upper,
        includes_upper=includes_upper,
        sample_count=len(value_tuple),
        mean_target_return_bps=mean,
        median_target_return_bps=Decimal(median(value_tuple)),
        confidence_lower_bps=lower_interval,
        confidence_upper_bps=upper_interval,
        empty=False,
    )


def _bootstrap_mean_interval(
    values: tuple[Decimal, ...],
    configuration: BootstrapConfiguration,
    *,
    seed_offset: int,
) -> tuple[Decimal, Decimal]:
    randomizer = random.Random(configuration.seed + seed_offset)
    means = []
    for _ in range(configuration.iterations):
        sample = tuple(values[randomizer.randrange(len(values))] for _ in values)
        means.append(sum(sample, Decimal(0)) / len(sample))
    tail = (1 - configuration.confidence_level) / 2
    return _percentile(tuple(means), tail), _percentile(tuple(means), 1 - tail)


def _wilson_interval(hits: int, count: int) -> tuple[float, float]:
    proportion = hits / count
    denominator = 1 + (_WILSON_Z_95**2 / count)
    center = (proportion + (_WILSON_Z_95**2 / (2 * count))) / denominator
    margin = (
        _WILSON_Z_95
        * math.sqrt(
            (proportion * (1 - proportion) / count)
            + (_WILSON_Z_95**2 / (4 * count**2))
        )
        / denominator
    )
    return center - margin, center + margin


@overload
def _percentile(values: Sequence[float], quantile: float) -> float: ...


@overload
def _percentile(values: Sequence[Decimal], quantile: float) -> Decimal: ...


def _percentile(
    values: Sequence[float] | Sequence[Decimal], quantile: float
) -> float | Decimal:
    if not values:
        raise ValueError("percentile requires at least one value")
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return cast(float | Decimal, ordered[lower])
    fraction = position - lower
    if isinstance(ordered[lower], Decimal):
        decimal_fraction = Decimal(str(fraction))
        lower_value = cast(Decimal, ordered[lower])
        upper_value = cast(Decimal, ordered[upper])
        return lower_value + (upper_value - lower_value) * decimal_fraction
    lower_float = cast(float, ordered[lower])
    upper_float = cast(float, ordered[upper])
    return lower_float + (upper_float - lower_float) * fraction


def _quarter(value: datetime) -> str:
    return f"{value.year}-Q{((value.month - 1) // 3) + 1}"
