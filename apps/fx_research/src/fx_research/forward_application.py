from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from fx_core import CurrencyPair
from fx_core.time import require_utc
from fx_signal_store import SQLiteSignalStore

from .forward import (
    ALIGNMENT_DELAY_MINUTES,
    ForwardJobStatus,
    MarketDataSource,
    UnsupportedProjectionError,
    schedule_forward_jobs,
)
from .forward_calculation import CandleAlignmentUnavailable, calculate_forward_result
from .forward_persistence import SQLiteForwardEvaluationStore


@dataclass(frozen=True, slots=True)
class ObserveForwardOnceResult:
    signals_scanned: int
    unsupported_signals: int
    jobs_scheduled: int
    due_jobs: int
    pending_jobs: int
    completed: int
    failed: int
    unavailable: int


class ObserveForwardOnceService:
    def __init__(
        self,
        signal_store: SQLiteSignalStore,
        forward_store: SQLiteForwardEvaluationStore,
        *,
        clock: Callable[[], datetime],
    ) -> None:
        self._signal_store = signal_store
        self._forward_store = forward_store
        self._clock = clock

    def run(
        self, source: MarketDataSource, *, instrument: CurrencyPair
    ) -> ObserveForwardOnceResult:
        now = self._clock()
        require_utc(now, "forward observation clock")
        signals = self._signal_store.list_signals()
        unsupported = 0
        scheduled = 0
        for signal in signals:
            try:
                jobs = schedule_forward_jobs(
                    signal,
                    market_source=source.source,
                    market_data_version=source.market_data_version,
                    instrument=instrument,
                )
            except UnsupportedProjectionError:
                unsupported += 1
                continue
            scheduled += self._forward_store.append_jobs(jobs, scheduled_at=now)

        due = 0
        pending = 0
        completed = 0
        failed = 0
        unavailable = 0
        retryable = self._forward_store.list_jobs(
            statuses=(ForwardJobStatus.PENDING, ForwardJobStatus.FAILED)
        )
        for record in retryable:
            job = record.job
            if (
                job.market_source != source.source
                or job.market_data_version != source.market_data_version
                or job.projection.instrument != instrument
            ):
                continue
            due_at = job.target_at + timedelta(minutes=ALIGNMENT_DELAY_MINUTES)
            if now < due_at:
                pending += 1
                continue
            due += 1
            try:
                candles = tuple(
                    source.fetch_candles(
                        instrument=job.projection.instrument,
                        granularity=job.granularity,
                        price_basis=job.price_basis,
                        start_at=job.anchor_at,
                        end_at=due_at + timedelta(minutes=1),
                    )
                )
                calculation = calculate_forward_result(
                    self._signal_store.get_signal(job.signal_id),
                    job,
                    candles,
                    completed_at=now,
                )
                self._forward_store.complete(
                    job.job_id,
                    snapshot=calculation.snapshot,
                    result=calculation.result,
                )
                completed += 1
            except CandleAlignmentUnavailable as error:
                self._forward_store.mark_unavailable(
                    job.job_id, reason=error.reason, updated_at=now
                )
                unavailable += 1
            except Exception as error:
                self._forward_store.mark_failed(job.job_id, error=error, updated_at=now)
                failed += 1
        return ObserveForwardOnceResult(
            signals_scanned=len(signals),
            unsupported_signals=unsupported,
            jobs_scheduled=scheduled,
            due_jobs=due,
            pending_jobs=pending,
            completed=completed,
            failed=failed,
            unavailable=unavailable,
        )
