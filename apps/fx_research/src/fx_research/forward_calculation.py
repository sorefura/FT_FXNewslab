import hashlib
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from fx_core import Signal

from .forward import (
    ALIGNMENT_DELAY_MINUTES,
    ForwardObservationJob,
    ForwardResult,
    MarketCandle,
    MarketSnapshot,
    UnavailableReason,
    market_granularity_duration,
)

_BPS = Decimal(10_000)


class CandleAlignmentUnavailable(RuntimeError):
    def __init__(self, reason: UnavailableReason) -> None:
        super().__init__(reason.value)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class ForwardCalculation:
    snapshot: MarketSnapshot
    result: ForwardResult


def calculate_forward_result(
    signal: Signal,
    job: ForwardObservationJob,
    candles: tuple[MarketCandle, ...],
    *,
    completed_at: datetime,
) -> ForwardCalculation:
    if job.signal_id != signal.signal_id:
        raise ValueError("forward job does not belong to the supplied Signal")
    if job.anchor_at != signal.created_at:
        raise ValueError("forward job must anchor at Signal.created_at")
    relevant = _ordered_complete_candles(job, candles)
    t0 = _aligned_candle(
        relevant,
        boundary=job.anchor_at,
        reason=UnavailableReason.T0_CANDLE_NOT_AVAILABLE,
    )
    tx = _aligned_candle(
        relevant,
        boundary=job.target_at,
        reason=UnavailableReason.TARGET_CANDLE_NOT_AVAILABLE,
    )
    evidence = tuple(
        candle for candle in relevant if t0.open_time <= candle.open_time <= tx.open_time
    )
    _validate_evidence_semantics(job, evidence)
    if completed_at < tx.open_time + market_granularity_duration(job.granularity):
        raise ValueError("ForwardResult cannot complete before its target candle closes")
    snapshot = MarketSnapshot(evidence)
    path = tuple(candle for candle in evidence if candle.open_time < tx.open_time)
    target_return_bps = (
        Decimal(job.projection.sign) * ((tx.open / t0.open) - Decimal(1)) * _BPS
    )
    mfe_bps, mae_bps = _directional_extrema(
        signal_direction=signal.direction.value,
        projection_sign=job.projection.sign,
        price_t0=t0.open,
        path=path,
    )
    result = ForwardResult(
        result_id="forward-result-" + hashlib.sha256(job.job_id.encode()).hexdigest(),
        signal_id=signal.signal_id,
        horizon=job.horizon,
        instrument=job.projection.instrument,
        projection_sign=job.projection.sign,
        projection_version=job.projection.version,
        anchor_at=job.anchor_at,
        target_at=job.target_at,
        price_t0=t0.open,
        price_tx=tx.open,
        t0_observed_at=t0.open_time,
        tx_observed_at=tx.open_time,
        target_return_bps=target_return_bps,
        mfe_bps=mfe_bps,
        mae_bps=mae_bps,
        realized_volatility=_realized_volatility(t0.open, path),
        completed_at=completed_at,
        market_source=job.market_source,
        market_data_version=job.market_data_version,
        price_basis=job.price_basis,
        granularity=job.granularity,
        formula_version=job.formula_version,
        snapshot_id=snapshot.snapshot_id,
    )
    return ForwardCalculation(snapshot, result)


def _ordered_complete_candles(
    job: ForwardObservationJob, candles: tuple[MarketCandle, ...]
) -> tuple[MarketCandle, ...]:
    latest_relevant = job.target_at + timedelta(minutes=ALIGNMENT_DELAY_MINUTES)
    return tuple(
        sorted(
            (
                item
                for item in candles
                if item.complete and job.anchor_at <= item.open_time <= latest_relevant
            ),
            key=lambda item: item.open_time,
        )
    )


def _validate_evidence_semantics(
    job: ForwardObservationJob, evidence: tuple[MarketCandle, ...]
) -> None:
    previous_time = None
    for candle in evidence:
        if (
            candle.source != job.market_source
            or candle.instrument != job.projection.instrument
            or candle.granularity != job.granularity
            or candle.price_basis != job.price_basis
            or candle.market_data_version != job.market_data_version
        ):
            raise ValueError("market candle semantics do not match forward job")
        if previous_time == candle.open_time:
            raise ValueError("market input has multiple revisions for one open time")
        previous_time = candle.open_time


def _aligned_candle(
    candles: tuple[MarketCandle, ...],
    *,
    boundary: datetime,
    reason: UnavailableReason,
) -> MarketCandle:
    latest = boundary + timedelta(minutes=ALIGNMENT_DELAY_MINUTES)
    try:
        return next(
            candle
            for candle in candles
            if boundary <= candle.open_time <= latest
        )
    except StopIteration as error:
        raise CandleAlignmentUnavailable(reason) from error


def _directional_extrema(
    *,
    signal_direction: float,
    projection_sign: int,
    price_t0: Decimal,
    path: tuple[MarketCandle, ...],
) -> tuple[Decimal | None, Decimal | None]:
    if signal_direction == 0:
        return None, None
    direction_sign = Decimal(1 if signal_direction > 0 else -1)
    projected_values = (
        direction_sign
        * Decimal(projection_sign)
        * ((price / price_t0) - Decimal(1))
        * _BPS
        for candle in path
        for price in (candle.high, candle.low)
    )
    values = tuple(projected_values)
    return max(Decimal(0), max(values)), min(Decimal(0), min(values))


def _realized_volatility(price_t0: Decimal, path: tuple[MarketCandle, ...]) -> float:
    prices = (price_t0,) + tuple(candle.close for candle in path)
    squared_returns = (
        math.log(float(current / previous)) ** 2
        for previous, current in zip(prices[:-1], prices[1:], strict=True)
    )
    return math.sqrt(sum(squared_returns))
