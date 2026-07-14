from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fx_research.evaluation import (
    BootstrapConfiguration,
    EvaluationExclusionReason,
    MetricConfiguration,
    UndefinedReason,
)
from fx_research.evaluation_metrics import (
    evaluate_cohort,
    excursion_summary,
    hit_rate,
    quarterly_stability,
    score_bucket_metrics,
    spearman_ic,
)

from tests.evaluation_factories import evaluation_sample

BOOTSTRAP = BootstrapConfiguration(seed=17, iterations=200)
METRICS = MetricConfiguration()


def test_perfect_positive_and_inverse_spearman_match_hand_calculation() -> None:
    returns = tuple(Decimal(value) for value in (10, 20, 30, 40))

    positive = spearman_ic((-1.0, -0.5, 0.5, 1.0), returns)
    inverse = spearman_ic((1.0, 0.5, -0.5, -1.0), returns)

    assert positive.value == pytest.approx(1.0)
    assert inverse.value == pytest.approx(-1.0)


def test_tied_scores_use_average_ranks() -> None:
    metric = spearman_ic(
        (1.0, 2.0, 2.0, 3.0),
        tuple(Decimal(value) for value in (1, 2, 3, 4)),
    )

    assert metric.value == pytest.approx(0.9486832980505138)


def test_insufficient_and_constant_inputs_are_undefined_not_zero() -> None:
    insufficient = spearman_ic((0.1, 0.2), (Decimal(1), Decimal(2)))
    constant_score = spearman_ic(
        (0.2, 0.2, 0.2), (Decimal(1), Decimal(2), Decimal(3))
    )
    constant_return = spearman_ic(
        (0.1, 0.2, 0.3), (Decimal(1), Decimal(1), Decimal(1))
    )

    assert insufficient.value is None
    assert insufficient.undefined_reason is UndefinedReason.INSUFFICIENT_SAMPLE
    assert constant_score.value is None
    assert constant_score.undefined_reason is UndefinedReason.CONSTANT_SCORE
    assert constant_return.value is None
    assert constant_return.undefined_reason is UndefinedReason.CONSTANT_RETURN


def test_deterministic_bootstrap_replays_identical_interval() -> None:
    scores = (-0.9, -0.4, 0.1, 0.5, 0.9)
    returns = tuple(Decimal(value) for value in (-4, -2, 1, 3, 5))

    first = spearman_ic(scores, returns, BOOTSTRAP)
    second = spearman_ic(scores, returns, BOOTSTRAP)

    assert first == second
    assert first.bootstrap_valid_iterations > 0
    assert first.confidence_lower is not None
    assert first.confidence_upper is not None


def test_hit_rate_excludes_neutral_and_zero_return_with_wilson_interval() -> None:
    metric = hit_rate(
        (1.0, -1.0, 0.0, 1.0, -1.0),
        tuple(Decimal(value) for value in (1, 1, 1, 0, -1)),
    )

    assert metric.total_sample_count == 5
    assert metric.eligible_sample_count == 3
    assert metric.hit_count == 2
    assert metric.neutral_signal_count == 1
    assert metric.zero_return_count == 1
    assert metric.value == pytest.approx(2 / 3)
    assert metric.confidence_lower == pytest.approx(0.2076596008020477)
    assert metric.confidence_upper == pytest.approx(0.9385080552796037)


def test_fixed_bucket_boundaries_and_empty_buckets_remain_explicit() -> None:
    scores = (-1.0, -0.6, -0.2, 0.2, 0.6, 1.0)
    returns = tuple(Decimal(value) for value in (1, 2, 3, 4, 5, 6))

    buckets, monotonicity = score_bucket_metrics(
        scores,
        returns,
        metric_configuration=METRICS,
        bootstrap_configuration=BOOTSTRAP,
    )
    empty_buckets, _ = score_bucket_metrics(
        (0.0,),
        (Decimal(1),),
        metric_configuration=METRICS,
        bootstrap_configuration=BOOTSTRAP,
    )

    assert tuple(item.sample_count for item in buckets) == (1, 1, 1, 1, 2)
    assert buckets[-1].includes_upper
    assert monotonicity.adjacent_step_ratio == 1.0
    assert tuple(item.empty for item in empty_buckets) == (True, True, False, True, True)


def test_non_monotonic_bucket_means_are_not_reported_as_fully_monotonic() -> None:
    buckets, monotonicity = score_bucket_metrics(
        (-0.9, -0.5, 0.0, 0.5, 0.9),
        tuple(Decimal(value) for value in (1, 3, 2, 5, 4)),
        metric_configuration=METRICS,
        bootstrap_configuration=BOOTSTRAP,
    )

    assert all(not item.empty for item in buckets)
    assert monotonicity.adjacent_non_decreasing_step_count == 2
    assert monotonicity.adjacent_step_ratio == 0.5
    assert monotonicity.bucket_mean_spearman != 1.0


def test_pair_scores_outside_fixed_bucket_range_are_counted_not_clamped() -> None:
    buckets, monotonicity = score_bucket_metrics(
        (-1.2, 0.0, 1.4),
        (Decimal(-2), Decimal(0), Decimal(2)),
        metric_configuration=METRICS,
        bootstrap_configuration=BOOTSTRAP,
    )

    assert sum(item.sample_count for item in buckets) == 1
    assert monotonicity.unbucketed_sample_count == 2


def test_null_mfe_and_mae_values_are_excluded_and_counted() -> None:
    summary = excursion_summary((Decimal(4), None, Decimal(8), None), METRICS)

    assert summary.eligible_count == 2
    assert summary.null_count == 2
    assert summary.mean_bps == Decimal(6)
    assert summary.median_bps == Decimal(6)
    assert summary.lower_quantile_bps == Decimal(5)
    assert summary.upper_quantile_bps == Decimal(7)


def test_quarterly_slices_preserve_sample_count_and_insufficient_state() -> None:
    samples = (
        evaluation_sample(1, -0.5, "-2"),
        evaluation_sample(2, 0.5, "2"),
        evaluation_sample(
            3,
            0.7,
            "3",
            created_at=datetime(2026, 4, 1, tzinfo=UTC),
        ),
    )

    slices = quarterly_stability(samples)

    assert tuple((item.period, item.sample_count) for item in slices) == (
        ("2026-Q1", 2),
        ("2026-Q2", 1),
    )
    assert all(
        item.undefined_reason is UndefinedReason.INSUFFICIENT_SAMPLE for item in slices
    )


def test_cohort_report_includes_sample_and_exclusion_diagnostics() -> None:
    samples = (
        evaluation_sample(1, -0.5, "-2", mfe_bps=None, mae_bps=None),
        evaluation_sample(2, 0.2, "0"),
        evaluation_sample(3, 0.8, "4"),
    )

    report = evaluate_cohort(
        samples,
        (
            EvaluationExclusionReason.PENDING_FORWARD_JOB,
            EvaluationExclusionReason.UNAVAILABLE_FORWARD_JOB,
        ),
        metric_configuration=METRICS,
        bootstrap_configuration=BOOTSTRAP,
    )

    diagnostics = report.metrics.diagnostics
    assert diagnostics.total_samples == 5
    assert diagnostics.included_samples == 3
    assert diagnostics.excluded_samples == 2
    assert report.metrics.mfe.null_count == 1
