from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

from fx_core import Horizon, SignalId
from fx_research.evaluation import CohortIdentity, EvaluationSample


def evaluation_cohort(**changes: object) -> CohortIdentity:
    source = CohortIdentity(
        signal_type="currency_fundamental",
        target_type="currency",
        target_value="USD",
        signal_horizon=Horizon.DAYS_3,
        forward_horizon=Horizon.DAY_1,
        producer_version="producer-v1",
        model_version="model-v1",
        prompt_version="prompt-v1",
        scorer_version="scorer-v1",
        transformation_version=None,
        market_source="gmo-fx-public-v1",
        market_data_version="gmo-fx-kline-bid-v1",
        price_basis="bid",
        granularity="M1",
        projection_version="currency-usdjpy-projection-v1",
        formula_version="forward-result-v1",
    )
    return replace(source, **changes)


def evaluation_sample(
    identifier: int,
    score: float,
    target_return_bps: str,
    *,
    cohort: CohortIdentity | None = None,
    mfe_bps: str | None = "4",
    mae_bps: str | None = "-2",
    created_at: datetime | None = None,
) -> EvaluationSample:
    observed_at = created_at or datetime(2026, 1, identifier, tzinfo=UTC)
    return EvaluationSample(
        signal_id=SignalId(f"signal-{identifier}"),
        forward_result_id=f"result-{identifier}",
        cohort=cohort or evaluation_cohort(),
        score=score,
        target_return_bps=Decimal(target_return_bps),
        mfe_bps=Decimal(mfe_bps) if mfe_bps is not None else None,
        mae_bps=Decimal(mae_bps) if mae_bps is not None else None,
        signal_created_at=observed_at,
        forward_completed_at=observed_at,
    )
