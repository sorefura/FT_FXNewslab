import math
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fx_core import Currency, CurrencyPair, CurrencyTarget, DirectionScore, Signal
from fx_research.forward import (
    ForwardObservationJob,
    MarketCandle,
    UnavailableReason,
    schedule_forward_jobs,
)
from fx_research.forward_calculation import (
    CandleAlignmentUnavailable,
    calculate_forward_result,
)

from tests.factories import signal

COMPLETED_AT = datetime(2026, 7, 20, tzinfo=UTC)


def _candle(
    open_time: datetime,
    *,
    open_price: str = "100",
    high: str = "101",
    low: str = "99",
    close: str | None = None,
    complete: bool = True,
) -> MarketCandle:
    return MarketCandle(
        source="oanda-v20",
        instrument=CurrencyPair.parse("USD_JPY"),
        granularity="M1",
        price_basis="midpoint",
        open_time=open_time,
        open=Decimal(open_price),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close if close is not None else open_price),
        complete=complete,
        market_data_version="oanda-v20-candles-v1",
    )


def _source(*, currency: str = "USD", direction: float = 0.6) -> Signal:
    original = signal()
    return Signal(
        signal_id=original.signal_id,
        target=CurrencyTarget(Currency(currency)),
        signal_type=original.signal_type,
        direction=DirectionScore(direction),
        strength=original.strength,
        confidence=original.confidence,
        horizon=original.horizon,
        observed_at=original.observed_at,
        created_at=original.created_at,
        source_feature_ids=original.source_feature_ids,
        versions=original.versions,
    )


def _job(source: Signal) -> ForwardObservationJob:
    return schedule_forward_jobs(
        source,
        market_source="oanda-v20",
        market_data_version="oanda-v20-candles-v1",
    )[0]


def test_alignment_uses_first_complete_open_at_or_after_each_boundary() -> None:
    source = _source()
    job = _job(source)
    candles = (
        _candle(job.anchor_at - timedelta(minutes=1), open_price="90", high="91", low="89"),
        _candle(job.anchor_at, open_price="95", high="96", low="94", complete=False),
        _candle(job.anchor_at + timedelta(minutes=1), open_price="100"),
        _candle(job.target_at, open_price="101", high="101", low="101", close="101"),
    )

    calculation = calculate_forward_result(source, job, candles, completed_at=COMPLETED_AT)

    assert calculation.result.price_t0 == Decimal("100")
    assert calculation.result.price_tx == Decimal("101")
    assert calculation.result.t0_observed_at == job.anchor_at + timedelta(minutes=1)
    assert calculation.result.tx_observed_at == job.target_at
    assert calculation.result.target_return_bps == Decimal("100")
    assert all(item.complete for item in calculation.snapshot.candles)


def test_alignment_accepts_five_minutes_but_not_six() -> None:
    source = _source()
    job = _job(source)
    t0 = _candle(job.anchor_at + timedelta(minutes=5))
    tx_at_limit = _candle(
        job.target_at + timedelta(minutes=5),
        open_price="101",
        high="101",
        low="101",
        close="101",
    )

    result = calculate_forward_result(
        source, job, (t0, tx_at_limit), completed_at=COMPLETED_AT
    )
    assert result.result.tx_observed_at == job.target_at + timedelta(minutes=5)

    with pytest.raises(CandleAlignmentUnavailable) as error:
        calculate_forward_result(
            source,
            job,
            (t0, _candle(job.target_at + timedelta(minutes=6))),
            completed_at=COMPLETED_AT,
        )
    assert error.value.reason is UnavailableReason.TARGET_CANDLE_NOT_AVAILABLE


def test_result_cannot_complete_before_aligned_tx_candle_closes() -> None:
    source = _source()
    job = _job(source)
    t0 = _candle(job.anchor_at)
    tx = _candle(job.target_at + timedelta(minutes=5))

    with pytest.raises(ValueError, match="closes"):
        calculate_forward_result(
            source,
            job,
            (t0, tx),
            completed_at=tx.open_time + timedelta(seconds=59),
        )

    calculation = calculate_forward_result(
        source,
        job,
        (t0, tx),
        completed_at=tx.open_time + timedelta(minutes=1),
    )
    assert calculation.result.completed_at == tx.open_time + timedelta(minutes=1)


def test_missing_entry_and_target_have_distinct_unavailable_reasons() -> None:
    source = _source()
    job = _job(source)

    with pytest.raises(CandleAlignmentUnavailable) as missing_t0:
        calculate_forward_result(
            source, job, (_candle(job.target_at),), completed_at=COMPLETED_AT
        )
    assert missing_t0.value.reason is UnavailableReason.T0_CANDLE_NOT_AVAILABLE

    with pytest.raises(CandleAlignmentUnavailable) as missing_tx:
        calculate_forward_result(
            source, job, (_candle(job.anchor_at),), completed_at=COMPLETED_AT
        )
    assert missing_tx.value.reason is UnavailableReason.TARGET_CANDLE_NOT_AVAILABLE


def test_target_return_depends_on_projection_not_signal_direction() -> None:
    positive = _source(direction=0.8)
    negative = _source(direction=-0.8)
    positive_job = _job(positive)
    negative_job = _job(negative)
    candles = (
        _candle(positive_job.anchor_at),
        _candle(
            positive_job.target_at,
            open_price="101",
            high="101",
            low="101",
            close="101",
        ),
    )

    positive_result = calculate_forward_result(
        positive, positive_job, candles, completed_at=COMPLETED_AT
    ).result
    negative_result = calculate_forward_result(
        negative, negative_job, candles, completed_at=COMPLETED_AT
    ).result

    assert positive_result.target_return_bps == Decimal("100")
    assert negative_result.target_return_bps == Decimal("100")


def test_jpy_projection_flips_market_return_without_pair_synthesis() -> None:
    source = _source(currency="JPY")
    job = _job(source)

    result = calculate_forward_result(
        source,
        job,
        (
            _candle(job.anchor_at),
            _candle(job.target_at, open_price="101", high="101", low="101", close="101"),
        ),
        completed_at=COMPLETED_AT,
    ).result

    assert result.projection_sign == -1
    assert result.target_return_bps == Decimal("-100")


def test_projection_sign_is_applied_before_directional_path_extrema() -> None:
    source = _source(currency="JPY", direction=0.8)
    job = _job(source)

    result = calculate_forward_result(
        source,
        job,
        (
            _candle(job.anchor_at, high="103", low="99"),
            _candle(job.target_at),
        ),
        completed_at=COMPLETED_AT,
    ).result

    assert result.mfe_bps == Decimal("100")
    assert result.mae_bps == Decimal("-300")


def test_directional_mfe_and_mae_use_path_high_low_and_exclude_tx_extremes() -> None:
    positive = _source(direction=0.8)
    negative = _source(direction=-0.8)
    job = _job(positive)
    candles = (
        _candle(job.anchor_at, high="103", low="99", close="101"),
        _candle(
            job.target_at,
            open_price="101",
            high="200",
            low="1",
            close="150",
        ),
    )

    positive_result = calculate_forward_result(
        positive, job, candles, completed_at=COMPLETED_AT
    ).result
    negative_result = calculate_forward_result(
        negative, _job(negative), candles, completed_at=COMPLETED_AT
    ).result

    assert (positive_result.mfe_bps, positive_result.mae_bps) == (
        Decimal("300"),
        Decimal("-100"),
    )
    assert (negative_result.mfe_bps, negative_result.mae_bps) == (
        Decimal("100"),
        Decimal("-300"),
    )


def test_neutral_signal_has_no_directional_mfe_or_mae() -> None:
    source = _source(direction=0)
    job = _job(source)

    result = calculate_forward_result(
        source,
        job,
        (_candle(job.anchor_at), _candle(job.target_at)),
        completed_at=COMPLETED_AT,
    ).result

    assert result.mfe_bps is None
    assert result.mae_bps is None


def test_realized_volatility_is_nonannualized_root_sum_squared_log_returns() -> None:
    source = _source()
    job = _job(source)
    candles = (
        _candle(job.anchor_at, close="101"),
        _candle(job.anchor_at + timedelta(minutes=1), high="102", close="102"),
        _candle(job.target_at, open_price="102", high="102", low="102", close="102"),
    )

    result = calculate_forward_result(
        source, job, candles, completed_at=COMPLETED_AT
    ).result

    expected = math.sqrt(math.log(101 / 100) ** 2 + math.log(102 / 101) ** 2)
    assert result.realized_volatility == pytest.approx(expected)


def test_future_candles_do_not_change_result_or_persisted_evidence() -> None:
    source = _source()
    job = _job(source)
    relevant = (
        _candle(job.anchor_at),
        _candle(job.target_at, open_price="101", high="101", low="101", close="101"),
    )
    future = replace(
        _candle(
            job.target_at + timedelta(minutes=1),
            open_price="200",
            high="300",
            low="1",
            close="250",
        ),
        source="unrelated-future-source",
    )

    without_future = calculate_forward_result(
        source, job, relevant, completed_at=COMPLETED_AT
    )
    with_future = calculate_forward_result(
        source, job, relevant + (future,), completed_at=COMPLETED_AT
    )

    assert with_future == without_future
    assert future not in with_future.snapshot.candles


def test_legitimate_zero_return_is_a_completed_numeric_result() -> None:
    source = _source()
    job = _job(source)

    result = calculate_forward_result(
        source,
        job,
        (_candle(job.anchor_at), _candle(job.target_at)),
        completed_at=COMPLETED_AT,
    ).result

    assert result.target_return_bps == Decimal("0")
