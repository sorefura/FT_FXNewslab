from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fx_core import (
    Currency,
    CurrencyPair,
    CurrencyTarget,
    DirectionScore,
    PairScore,
    PairTarget,
    Signal,
)
from fx_research.forward import (
    FORWARD_HORIZONS,
    MarketCandle,
    MarketSnapshot,
    UnsupportedProjectionError,
    resolve_projection,
    schedule_forward_jobs,
)

from tests.factories import signal


def _candle(open_time: datetime, *, close: str = "150.10") -> MarketCandle:
    return MarketCandle(
        source="oanda-v20",
        instrument=CurrencyPair.parse("USD_JPY"),
        granularity="M1",
        price_basis="midpoint",
        open_time=open_time,
        open=Decimal("150.00"),
        high=Decimal("150.20"),
        low=Decimal("149.90"),
        close=Decimal(close),
        complete=True,
        market_data_version="oanda-v20-candles-v1",
    )


def test_every_signal_schedules_all_five_forward_horizons_from_created_at() -> None:
    source = signal()

    jobs = schedule_forward_jobs(
        source,
        market_source="oanda-v20",
        market_data_version="oanda-v20-candles-v1",
    )

    assert tuple(job.horizon for job in jobs) == FORWARD_HORIZONS
    assert all(job.anchor_at == source.created_at for job in jobs)
    assert tuple(job.target_at for job in jobs) == tuple(
        source.created_at + horizon.duration for horizon in FORWARD_HORIZONS
    )


@pytest.mark.parametrize(
    ("target", "direction", "expected_sign"),
    [
        (CurrencyTarget(Currency("USD")), DirectionScore(0.4), 1),
        (CurrencyTarget(Currency("JPY")), DirectionScore(0.4), -1),
        (PairTarget(CurrencyPair.parse("USD_JPY")), PairScore(0.4), 1),
    ],
)
def test_projection_maps_one_supported_signal_without_pair_synthesis(
    target: CurrencyTarget | PairTarget,
    direction: DirectionScore | PairScore,
    expected_sign: int,
) -> None:
    source = signal()
    projected = Signal(
        signal_id=source.signal_id,
        target=target,
        signal_type=source.signal_type,
        direction=direction,
        strength=source.strength,
        confidence=source.confidence,
        horizon=source.horizon,
        observed_at=source.observed_at,
        created_at=source.created_at,
        source_feature_ids=source.source_feature_ids,
        versions=source.versions,
    )

    projection = resolve_projection(projected)

    assert projection.instrument == CurrencyPair.parse("USD_JPY")
    assert projection.sign == expected_sign
    assert projection.version == "currency-usdjpy-projection-v1"


@pytest.mark.parametrize(
    ("target", "direction"),
    [
        (CurrencyTarget(Currency("EUR")), DirectionScore(0.4)),
        (PairTarget(CurrencyPair.parse("EUR_USD")), PairScore(0.4)),
    ],
)
def test_unsupported_projection_is_not_coerced_to_zero(
    target: CurrencyTarget | PairTarget, direction: DirectionScore | PairScore
) -> None:
    source = signal()
    unsupported = Signal(
        signal_id=source.signal_id,
        target=target,
        signal_type=source.signal_type,
        direction=direction,
        strength=source.strength,
        confidence=source.confidence,
        horizon=source.horizon,
        observed_at=source.observed_at,
        created_at=source.created_at,
        source_feature_ids=source.source_feature_ids,
        versions=source.versions,
    )

    with pytest.raises(UnsupportedProjectionError):
        resolve_projection(unsupported)


def test_market_candle_revision_changes_when_same_time_content_changes() -> None:
    open_time = datetime(2026, 7, 14, 0, 0, tzinfo=UTC)

    original = _candle(open_time)
    corrected = _candle(open_time, close="150.11")

    assert original.revision_id != corrected.revision_id


def test_market_snapshot_requires_complete_ordered_consistent_evidence() -> None:
    open_time = datetime(2026, 7, 14, 0, 0, tzinfo=UTC)
    first = _candle(open_time)
    second = _candle(open_time + timedelta(minutes=1))

    snapshot = MarketSnapshot((first, second))

    assert snapshot.candles == (first, second)
    assert snapshot.snapshot_id.startswith("snapshot-")
    with pytest.raises(FrozenInstanceError):
        snapshot.candles = ()  # type: ignore[misc]


def test_market_candle_rejects_naive_time_and_inconsistent_ohlc() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        _candle(datetime(2026, 7, 14, 0, 0))

    with pytest.raises(ValueError, match="OHLC"):
        MarketCandle(
            source="oanda-v20",
            instrument=CurrencyPair.parse("USD_JPY"),
            granularity="M1",
            price_basis="midpoint",
            open_time=datetime(2026, 7, 14, 0, 0, tzinfo=UTC),
            open=Decimal("150.00"),
            high=Decimal("149.99"),
            low=Decimal("149.90"),
            close=Decimal("150.10"),
            complete=True,
            market_data_version="oanda-v20-candles-v1",
        )


def test_forward_contracts_remain_research_only() -> None:
    assert MarketCandle.__module__.startswith("fx_research")
