from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fx_core import CurrencyPair, Horizon, VersionMetadata
from fx_research.evaluation import (
    EvaluationExclusionReason,
    cohort_identity,
    evaluation_sample,
    exclusion_reason,
    group_samples,
)
from fx_research.forward import (
    ForwardJobStatus,
    ForwardResult,
    schedule_forward_jobs,
)

from tests.factories import signal

NOW = datetime(2026, 7, 20, tzinfo=UTC)


def _result(**changes: object) -> ForwardResult:
    source = signal()
    job = schedule_forward_jobs(
        source,
        market_source="gmo-fx-public-v1",
        market_data_version="gmo-fx-kline-bid-v1",
        price_basis="bid",
    )[3]
    result = ForwardResult(
        result_id="result-1",
        signal_id=source.signal_id,
        horizon=job.horizon,
        instrument=CurrencyPair.parse("USD_JPY"),
        projection_sign=job.projection.sign,
        projection_version=job.projection.version,
        anchor_at=job.anchor_at,
        target_at=job.target_at,
        price_t0=Decimal("150"),
        price_tx=Decimal("151"),
        t0_observed_at=job.anchor_at,
        tx_observed_at=job.target_at,
        target_return_bps=Decimal("66.6666666667"),
        mfe_bps=Decimal("80"),
        mae_bps=Decimal("-20"),
        realized_volatility=0.01,
        completed_at=NOW,
        market_source=job.market_source,
        market_data_version=job.market_data_version,
        price_basis=job.price_basis,
        granularity=job.granularity,
        formula_version=job.formula_version,
        snapshot_id="snapshot-1",
    )
    return replace(result, **changes)


def test_signal_direction_is_read_without_mutating_signal_or_forward_result() -> None:
    source_signal = signal()
    source_result = _result()

    sample = evaluation_sample(source_signal, source_result)

    assert sample.score == 0.6
    assert sample.target_return_bps == source_result.target_return_bps
    assert source_signal == signal()
    assert source_result == _result()
    with pytest.raises(FrozenInstanceError):
        sample.score = 1.0  # type: ignore[misc]


@pytest.mark.parametrize(
    "changed_signal",
    [
        replace(
            signal(),
            versions=replace(signal().versions, scorer_version="scorer-v2"),
        ),
        replace(
            signal(),
            versions=replace(signal().versions, model_version="model-v2"),
        ),
        replace(
            signal(),
            versions=replace(signal().versions, prompt_version="prompt-v2"),
        ),
        replace(
            signal(),
            versions=VersionMetadata(
                producer_version="producer-v1",
                model_version="model-v1",
                prompt_version="prompt-v1",
                scorer_version="scorer-v1",
                transformation_version="pair-v1",
            ),
        ),
    ],
)
def test_signal_semantic_versions_create_separate_cohorts(changed_signal) -> None:  # type: ignore[no-untyped-def]
    original = cohort_identity(signal(), _result())

    changed = cohort_identity(changed_signal, _result())

    assert changed.cohort_id != original.cohort_id


@pytest.mark.parametrize(
    "changes",
    [
        {
            "market_source": "oanda-v20",
            "market_data_version": "oanda-v20-candles-v1",
            "price_basis": "midpoint",
        },
        {"projection_version": "projection-v2"},
        {"formula_version": "forward-result-v2"},
    ],
)
def test_market_projection_and_formula_versions_create_separate_cohorts(
    changes: dict[str, object],
) -> None:
    original = cohort_identity(signal(), _result())

    changed = cohort_identity(signal(), _result(**changes))

    assert changed.cohort_id != original.cohort_id


def test_signal_horizon_and_forward_horizon_are_distinct_cohort_dimensions() -> None:
    source = signal()
    original = cohort_identity(source, _result())

    changed_signal_horizon = cohort_identity(
        replace(source, horizon=Horizon.HOUR_1), _result()
    )
    changed_forward_horizon = cohort_identity(
        source, replace(_result(), horizon=Horizon.HOURS_4)
    )

    assert original.signal_horizon is Horizon.DAYS_3
    assert original.forward_horizon is Horizon.DAY_1
    assert len(
        {
            original.cohort_id,
            changed_signal_horizon.cohort_id,
            changed_forward_horizon.cohort_id,
        }
    ) == 3


def test_non_completed_forward_states_are_explicit_exclusions() -> None:
    assert exclusion_reason(ForwardJobStatus.PENDING) is (
        EvaluationExclusionReason.PENDING_FORWARD_JOB
    )
    assert exclusion_reason(ForwardJobStatus.FAILED) is (
        EvaluationExclusionReason.FAILED_FORWARD_JOB
    )
    assert exclusion_reason(ForwardJobStatus.UNAVAILABLE) is (
        EvaluationExclusionReason.UNAVAILABLE_FORWARD_JOB
    )
    with pytest.raises(ValueError, match="not an exclusion"):
        exclusion_reason(ForwardJobStatus.COMPLETED)


def test_grouping_never_combines_gmo_bid_and_oanda_midpoint() -> None:
    gmo = evaluation_sample(signal(), _result())
    oanda_result = _result(
        result_id="result-oanda",
        market_source="oanda-v20",
        market_data_version="oanda-v20-candles-v1",
        price_basis="midpoint",
    )
    oanda = evaluation_sample(signal(), oanda_result)

    grouped = group_samples(tuple(sorted((gmo, oanda), key=lambda item: item.input_identity)))

    assert len(grouped) == 2
    assert {item[0].price_basis for item in grouped} == {"bid", "midpoint"}
