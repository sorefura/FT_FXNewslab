from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from fx_core import CurrencyPair
from fx_research.forward import (
    ForwardJobStatus,
    MarketCandle,
    UnavailableReason,
)
from fx_research.forward_application import ObserveForwardOnceService
from fx_research.forward_calculation import calculate_forward_result
from fx_research.forward_persistence import SQLiteForwardEvaluationStore
from fx_signal_store import SQLiteSignalStore

from tests.factories import feature, observation, signal

NOW = datetime(2026, 7, 20, tzinfo=UTC)


class RecordedMarketSource:
    source = "oanda-v20"
    market_data_version = "oanda-v20-candles-v1"

    def __init__(self, *, fail: bool = False, omit_target: bool = False) -> None:
        self.fail = fail
        self.omit_target = omit_target
        self.calls = 0

    def fetch_candles(
        self,
        *,
        instrument: CurrencyPair,
        granularity: str,
        price_basis: str,
        start_at: datetime,
        end_at: datetime,
    ) -> tuple[MarketCandle, ...]:
        self.calls += 1
        if self.fail:
            raise TimeoutError("Authorization: Bearer synthetic-secret")
        target_at = end_at - timedelta(minutes=6)
        candles = [self._candle(start_at, instrument, granularity, price_basis)]
        if not self.omit_target:
            candles.append(
                self._candle(
                    target_at,
                    instrument,
                    granularity,
                    price_basis,
                    open_price="101",
                )
            )
        return tuple(candles)

    def _candle(
        self,
        open_time: datetime,
        instrument: CurrencyPair,
        granularity: str,
        price_basis: str,
        *,
        open_price: str = "100",
    ) -> MarketCandle:
        price = Decimal(open_price)
        return MarketCandle(
            source=self.source,
            instrument=instrument,
            granularity=granularity,
            price_basis=price_basis,
            open_time=open_time,
            open=price,
            high=price,
            low=price,
            close=price,
            complete=True,
            market_data_version=self.market_data_version,
        )


def _stores(database: Path) -> tuple[SQLiteSignalStore, SQLiteForwardEvaluationStore]:
    signal_store = SQLiteSignalStore(database)
    signal_store.append_observation(observation())
    signal_store.append_feature(feature())
    signal_store.append_signal(signal())
    return signal_store, SQLiteForwardEvaluationStore(database)


def test_one_signal_completes_five_jobs_and_idempotent_rerun_makes_no_calls(
    tmp_path: Path,
) -> None:
    signal_store, forward_store = _stores(tmp_path / "research.db")
    source = RecordedMarketSource()
    service = ObserveForwardOnceService(
        signal_store, forward_store, clock=lambda: NOW
    )
    persisted_signal_before = signal_store.get_signal(signal().signal_id)

    first = service.run(source, instrument=CurrencyPair.parse("USD_JPY"))
    calls_after_first = source.calls
    second = service.run(source, instrument=CurrencyPair.parse("USD_JPY"))

    assert first.jobs_scheduled == 5
    assert first.due_jobs == 5
    assert first.completed == 5
    assert len(forward_store.list_results(signal_id=signal().signal_id)) == 5
    assert calls_after_first == 5
    assert source.calls == calls_after_first
    assert second.jobs_scheduled == 0
    assert second.due_jobs == 0
    assert signal_store.get_signal(signal().signal_id) == persisted_signal_before


def test_pending_jobs_do_not_call_market_provider_before_target_delay(tmp_path: Path) -> None:
    signal_store, forward_store = _stores(tmp_path / "research.db")
    source = RecordedMarketSource()

    result = ObserveForwardOnceService(
        signal_store,
        forward_store,
        clock=lambda: signal().created_at + timedelta(minutes=10),
    ).run(source, instrument=CurrencyPair.parse("USD_JPY"))

    assert result.pending_jobs == 5
    assert result.due_jobs == 0
    assert source.calls == 0


def test_alignment_window_end_remains_pending_until_m1_candle_can_close(
    tmp_path: Path,
) -> None:
    signal_store, forward_store = _stores(tmp_path / "research.db")
    source = RecordedMarketSource()

    result = ObserveForwardOnceService(
        signal_store,
        forward_store,
        clock=lambda: signal().created_at + timedelta(minutes=20),
    ).run(source, instrument=CurrencyPair.parse("USD_JPY"))

    assert result.pending_jobs == 5
    assert result.due_jobs == 0
    assert source.calls == 0


def test_provider_failure_is_failed_and_does_not_create_result(tmp_path: Path) -> None:
    signal_store, forward_store = _stores(tmp_path / "research.db")

    result = ObserveForwardOnceService(
        signal_store, forward_store, clock=lambda: NOW
    ).run(RecordedMarketSource(fail=True), instrument=CurrencyPair.parse("USD_JPY"))

    assert result.failed == 5
    assert not forward_store.list_results()
    records = forward_store.list_jobs(statuses=(ForwardJobStatus.FAILED,))
    assert len(records) == 5
    assert all("synthetic-secret" not in (item.error_message or "") for item in records)


def test_due_missing_target_is_unavailable_not_zero_result(tmp_path: Path) -> None:
    signal_store, forward_store = _stores(tmp_path / "research.db")

    result = ObserveForwardOnceService(
        signal_store, forward_store, clock=lambda: NOW
    ).run(
        RecordedMarketSource(omit_target=True),
        instrument=CurrencyPair.parse("USD_JPY"),
    )

    assert result.unavailable == 5
    assert not forward_store.list_results()
    records = forward_store.list_jobs(statuses=(ForwardJobStatus.UNAVAILABLE,))
    assert {
        item.unavailable_reason for item in records
    } == {UnavailableReason.TARGET_CANDLE_NOT_AVAILABLE}


def test_persisted_snapshot_recalculates_identical_result_offline(tmp_path: Path) -> None:
    signal_store, forward_store = _stores(tmp_path / "research.db")
    ObserveForwardOnceService(signal_store, forward_store, clock=lambda: NOW).run(
        RecordedMarketSource(), instrument=CurrencyPair.parse("USD_JPY")
    )
    record = forward_store.list_jobs(statuses=(ForwardJobStatus.COMPLETED,))[0]
    assert record.result_id is not None
    persisted = forward_store.get_result(record.result_id)
    evidence = forward_store.get_snapshot(persisted.snapshot_id)

    replay = calculate_forward_result(
        signal_store.get_signal(record.job.signal_id),
        record.job,
        evidence.candles,
        completed_at=persisted.completed_at,
    )

    assert replay.result == persisted
    assert replay.snapshot == evidence
