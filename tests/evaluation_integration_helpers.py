from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from fx_core import CurrencyPair, DirectionScore
from fx_research.forward import ForwardResult, MarketCandle, MarketSnapshot, schedule_forward_jobs
from fx_research.forward_persistence import SQLiteForwardEvaluationStore
from fx_signal_store import SQLiteSignalStore

from tests.factories import feature, observation, signal

COMPLETED_AT = datetime(2026, 7, 20, tzinfo=UTC)


def seed_evaluation_database(
    database: Path,
    score_returns: tuple[tuple[float, str], ...] = ((0.6, "5"),),
) -> None:
    signal_store = SQLiteSignalStore(database)
    forward_store = SQLiteForwardEvaluationStore(database)
    for index, (score, target_return) in enumerate(score_returns, start=1):
        observation_id = f"obs-evaluation-{index}"
        feature_id = f"feature-evaluation-{index}"
        signal_id = f"signal-evaluation-{index}"
        source_observation = observation(observation_id)
        source_feature = feature(feature_id, observation_id)
        source_signal = replace(
            signal(signal_id, feature_id),
            direction=DirectionScore(score),
            observed_at=signal().observed_at + timedelta(minutes=index),
            created_at=signal().created_at + timedelta(minutes=index),
        )
        signal_store.append_observation(source_observation)
        signal_store.append_feature(source_feature)
        signal_store.append_signal(source_signal)
        jobs = schedule_forward_jobs(
            source_signal,
            market_source="gmo-fx-public-v1",
            market_data_version="gmo-fx-kline-bid-v1",
            price_basis="bid",
        )
        forward_store.append_jobs(jobs, scheduled_at=COMPLETED_AT)
        _complete(
            forward_store,
            jobs[0],
            result_id=f"result-evaluation-{index}",
            target_return_bps=Decimal(target_return),
            neutral=score == 0,
        )


def _complete(
    store: SQLiteForwardEvaluationStore,
    job,  # type: ignore[no-untyped-def]
    *,
    result_id: str,
    target_return_bps: Decimal,
    neutral: bool,
) -> None:
    first = _candle(job.anchor_at, "150")
    last = _candle(job.target_at, "151")
    snapshot = MarketSnapshot((first, last))
    result = ForwardResult(
        result_id=result_id,
        signal_id=job.signal_id,
        horizon=job.horizon,
        instrument=job.projection.instrument,
        projection_sign=job.projection.sign,
        projection_version=job.projection.version,
        anchor_at=job.anchor_at,
        target_at=job.target_at,
        price_t0=first.open,
        price_tx=last.open,
        t0_observed_at=first.open_time,
        tx_observed_at=last.open_time,
        target_return_bps=target_return_bps,
        mfe_bps=None if neutral else Decimal("8"),
        mae_bps=None if neutral else Decimal("-3"),
        realized_volatility=0.01,
        completed_at=COMPLETED_AT,
        market_source=job.market_source,
        market_data_version=job.market_data_version,
        price_basis=job.price_basis,
        granularity=job.granularity,
        formula_version=job.formula_version,
        snapshot_id=snapshot.snapshot_id,
    )
    store.complete(job.job_id, snapshot=snapshot, result=result)


def _candle(open_time: datetime, price: str) -> MarketCandle:
    value = Decimal(price)
    return MarketCandle(
        source="gmo-fx-public-v1",
        instrument=CurrencyPair.parse("USD_JPY"),
        granularity="M1",
        price_basis="bid",
        open_time=open_time,
        open=value,
        high=value,
        low=value,
        close=value,
        complete=True,
        market_data_version="gmo-fx-kline-bid-v1",
    )
