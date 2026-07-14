import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from fx_core import CurrencyPair, CurrencyTarget, Horizon, PairTarget, Signal, SignalId
from fx_core.time import require_utc

FORWARD_HORIZONS = (
    Horizon.MINUTES_15,
    Horizon.HOUR_1,
    Horizon.HOURS_4,
    Horizon.DAY_1,
    Horizon.DAYS_3,
)
PROJECTION_VERSION = "currency-usdjpy-projection-v1"
FORMULA_VERSION = "forward-result-v1"
DEFAULT_INSTRUMENT = CurrencyPair.parse("USD_JPY")
DEFAULT_GRANULARITY = "M1"
DEFAULT_PRICE_BASIS = "midpoint"
ALIGNMENT_DELAY_MINUTES = 5


class UnsupportedProjectionError(ValueError):
    pass


class ForwardJobStatus(StrEnum):
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    UNAVAILABLE = "UNAVAILABLE"


class UnavailableReason(StrEnum):
    T0_CANDLE_NOT_AVAILABLE = "T0_CANDLE_NOT_AVAILABLE"
    TARGET_CANDLE_NOT_AVAILABLE = "TARGET_CANDLE_NOT_AVAILABLE"


@dataclass(frozen=True, slots=True)
class ForwardProjection:
    instrument: CurrencyPair
    sign: int
    version: str

    def __post_init__(self) -> None:
        if self.sign not in (-1, 1):
            raise ValueError("projection sign must be -1 or 1")
        _require_text(self.version, "projection version")


@dataclass(frozen=True, slots=True)
class MarketCandle:
    source: str
    instrument: CurrencyPair
    granularity: str
    price_basis: str
    open_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    complete: bool
    market_data_version: str

    def __post_init__(self) -> None:
        for value, label in (
            (self.source, "market source"),
            (self.granularity, "market granularity"),
            (self.price_basis, "market price basis"),
            (self.market_data_version, "market data version"),
        ):
            _require_text(value, label)
        require_utc(self.open_time, "candle.open_time")
        prices = (self.open, self.high, self.low, self.close)
        if any(price <= 0 for price in prices):
            raise ValueError("candle prices must be positive")
        if self.low > min(self.open, self.close) or self.high < max(
            self.open, self.close
        ):
            raise ValueError("candle OHLC is inconsistent")
        if self.low > self.high:
            raise ValueError("candle low must not exceed high")

    @property
    def revision_id(self) -> str:
        return "candle-" + _digest(
            self.source,
            self.instrument.symbol,
            self.granularity,
            self.price_basis,
            self.open_time.isoformat(),
            *(_decimal_text(value) for value in (self.open, self.high, self.low, self.close)),
            str(self.complete),
            self.market_data_version,
        )


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    candles: tuple[MarketCandle, ...]

    def __post_init__(self) -> None:
        if not self.candles:
            raise ValueError("MarketSnapshot requires at least one candle")
        first = self.candles[0]
        semantics = (
            first.source,
            first.instrument,
            first.granularity,
            first.price_basis,
            first.market_data_version,
        )
        previous: datetime | None = None
        for candle in self.candles:
            if not candle.complete:
                raise ValueError("MarketSnapshot accepts complete candles only")
            if (
                candle.source,
                candle.instrument,
                candle.granularity,
                candle.price_basis,
                candle.market_data_version,
            ) != semantics:
                raise ValueError("MarketSnapshot candles must share market semantics")
            if previous is not None and candle.open_time <= previous:
                raise ValueError("MarketSnapshot candles must be strictly ordered")
            previous = candle.open_time

    @property
    def snapshot_id(self) -> str:
        return "snapshot-" + _digest(*(item.revision_id for item in self.candles))


@dataclass(frozen=True, slots=True)
class ForwardObservationJob:
    job_id: str
    signal_id: SignalId
    horizon: Horizon
    projection: ForwardProjection
    anchor_at: datetime
    target_at: datetime
    market_source: str
    granularity: str
    price_basis: str
    market_data_version: str
    formula_version: str = FORMULA_VERSION

    def __post_init__(self) -> None:
        _require_text(self.job_id, "forward job id")
        require_utc(self.anchor_at, "forward job anchor_at")
        require_utc(self.target_at, "forward job target_at")
        if self.target_at != self.anchor_at + self.horizon.duration:
            raise ValueError("forward job target must equal anchor plus horizon")
        for value, label in (
            (self.market_source, "market source"),
            (self.granularity, "market granularity"),
            (self.price_basis, "market price basis"),
            (self.market_data_version, "market data version"),
            (self.formula_version, "formula version"),
        ):
            _require_text(value, label)


@dataclass(frozen=True, slots=True)
class ForwardJobRecord:
    job: ForwardObservationJob
    status: ForwardJobStatus
    updated_at: datetime
    unavailable_reason: UnavailableReason | None = None
    error_code: str | None = None
    error_message: str | None = None
    result_id: str | None = None

    def __post_init__(self) -> None:
        require_utc(self.updated_at, "forward job updated_at")
        if self.status is ForwardJobStatus.COMPLETED and self.result_id is None:
            raise ValueError("completed forward job requires result_id")
        if self.status is not ForwardJobStatus.COMPLETED and self.result_id is not None:
            raise ValueError("only completed forward job can reference a result")
        if (
            self.status is ForwardJobStatus.UNAVAILABLE
            and self.unavailable_reason is None
        ):
            raise ValueError("unavailable forward job requires a reason")
        if (
            self.status is not ForwardJobStatus.UNAVAILABLE
            and self.unavailable_reason is not None
        ):
            raise ValueError("only unavailable forward job can have an unavailable reason")


@dataclass(frozen=True, slots=True)
class ForwardResult:
    result_id: str
    signal_id: SignalId
    horizon: Horizon
    instrument: CurrencyPair
    projection_sign: int
    projection_version: str
    anchor_at: datetime
    target_at: datetime
    price_t0: Decimal
    price_tx: Decimal
    t0_observed_at: datetime
    tx_observed_at: datetime
    target_return_bps: Decimal
    mfe_bps: Decimal | None
    mae_bps: Decimal | None
    realized_volatility: float
    completed_at: datetime
    market_source: str
    market_data_version: str
    price_basis: str
    granularity: str
    formula_version: str
    snapshot_id: str

    def __post_init__(self) -> None:
        for text_value, label in (
            (self.result_id, "forward result id"),
            (self.projection_version, "projection version"),
            (self.market_source, "market source"),
            (self.market_data_version, "market data version"),
            (self.price_basis, "price basis"),
            (self.granularity, "granularity"),
            (self.formula_version, "formula version"),
            (self.snapshot_id, "snapshot id"),
        ):
            _require_text(text_value, label)
        if self.projection_sign not in (-1, 1):
            raise ValueError("projection sign must be -1 or 1")
        for timestamp, label in (
            (self.anchor_at, "result anchor_at"),
            (self.target_at, "result target_at"),
            (self.t0_observed_at, "result t0_observed_at"),
            (self.tx_observed_at, "result tx_observed_at"),
            (self.completed_at, "result completed_at"),
        ):
            require_utc(timestamp, label)
        if self.price_t0 <= 0 or self.price_tx <= 0:
            raise ValueError("result prices must be positive")
        if self.realized_volatility < 0:
            raise ValueError("realized volatility must not be negative")


class MarketDataSource(Protocol):
    source: str
    market_data_version: str

    def fetch_candles(
        self,
        *,
        instrument: CurrencyPair,
        granularity: str,
        price_basis: str,
        start_at: datetime,
        end_at: datetime,
    ) -> Sequence[MarketCandle]: ...


def resolve_projection(
    signal: Signal, instrument: CurrencyPair = DEFAULT_INSTRUMENT
) -> ForwardProjection:
    if instrument != DEFAULT_INSTRUMENT:
        raise UnsupportedProjectionError(
            f"unsupported forward evaluation instrument: {instrument.symbol}"
        )
    if isinstance(signal.target, CurrencyTarget):
        signs = {"USD": 1, "JPY": -1}
        sign = signs.get(signal.target.currency.code)
        if sign is None:
            raise UnsupportedProjectionError(
                f"unsupported Currency target: {signal.target.currency.code}"
            )
    elif isinstance(signal.target, PairTarget) and signal.target.pair == instrument:
        sign = 1
    else:
        target = signal.target.pair.symbol if isinstance(signal.target, PairTarget) else "unknown"
        raise UnsupportedProjectionError(f"unsupported Pair target: {target}")
    return ForwardProjection(instrument, sign, PROJECTION_VERSION)


def schedule_forward_jobs(
    signal: Signal,
    *,
    market_source: str,
    market_data_version: str,
    instrument: CurrencyPair = DEFAULT_INSTRUMENT,
    granularity: str = DEFAULT_GRANULARITY,
    price_basis: str = DEFAULT_PRICE_BASIS,
) -> tuple[ForwardObservationJob, ...]:
    projection = resolve_projection(signal, instrument)
    jobs = []
    for horizon in FORWARD_HORIZONS:
        target_at = signal.created_at + horizon.duration
        job_id = "forward-job-" + _digest(
            signal.signal_id.value,
            horizon.value,
            projection.instrument.symbol,
            str(projection.sign),
            projection.version,
            market_source,
            market_data_version,
            granularity,
            price_basis,
            FORMULA_VERSION,
        )
        jobs.append(
            ForwardObservationJob(
                job_id=job_id,
                signal_id=signal.signal_id,
                horizon=horizon,
                projection=projection,
                anchor_at=signal.created_at,
                target_at=target_at,
                market_source=market_source,
                granularity=granularity,
                price_basis=price_basis,
                market_data_version=market_data_version,
            )
        )
    return tuple(jobs)


def _require_text(value: str, label: str) -> None:
    if not value.strip():
        raise ValueError(f"{label} must not be blank")


def _decimal_text(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _digest(*parts: str) -> str:
    return hashlib.sha256("\0".join(parts).encode()).hexdigest()
