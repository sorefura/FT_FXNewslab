import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fx_core import CurrencyPair
from fx_research.forward import (
    ForwardJobStatus,
    ForwardResult,
    MarketCandle,
    MarketSnapshot,
    UnavailableReason,
    schedule_forward_jobs,
)
from fx_research.forward_persistence import SQLiteForwardEvaluationStore

from tests.factories import signal

NOW = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)


def _candle(open_time: datetime, *, close: str = "150.05") -> MarketCandle:
    return MarketCandle(
        source="oanda-v20",
        instrument=CurrencyPair.parse("USD_JPY"),
        granularity="M1",
        price_basis="midpoint",
        open_time=open_time,
        open=Decimal("150.00"),
        high=Decimal("150.10"),
        low=Decimal("149.90"),
        close=Decimal(close),
        complete=True,
        market_data_version="oanda-v20-candles-v1",
    )


def _result(job_id: str, snapshot: MarketSnapshot) -> ForwardResult:
    source = signal()
    job = next(
        item
        for item in schedule_forward_jobs(
            source,
            market_source="oanda-v20",
            market_data_version="oanda-v20-candles-v1",
        )
        if item.job_id == job_id
    )
    return ForwardResult(
        result_id="result-" + job.job_id,
        signal_id=job.signal_id,
        horizon=job.horizon,
        instrument=job.projection.instrument,
        projection_sign=job.projection.sign,
        projection_version=job.projection.version,
        anchor_at=job.anchor_at,
        target_at=job.target_at,
        price_t0=snapshot.candles[0].open,
        price_tx=snapshot.candles[-1].open,
        t0_observed_at=snapshot.candles[0].open_time,
        tx_observed_at=snapshot.candles[-1].open_time,
        target_return_bps=Decimal("3.333333333333333333333333000"),
        mfe_bps=Decimal("6.666666666666666666666667000"),
        mae_bps=Decimal("-6.666666666666666666666667000"),
        realized_volatility=0.0003,
        completed_at=NOW,
        market_source=job.market_source,
        market_data_version=job.market_data_version,
        price_basis=job.price_basis,
        granularity=job.granularity,
        formula_version=job.formula_version,
        snapshot_id=snapshot.snapshot_id,
    )


def _jobs():  # type: ignore[no-untyped-def]
    return schedule_forward_jobs(
        signal(),
        market_source="oanda-v20",
        market_data_version="oanda-v20-candles-v1",
    )


def test_forward_jobs_are_scheduled_idempotently_with_pending_state(tmp_path) -> None:
    store = SQLiteForwardEvaluationStore(tmp_path / "research.db")

    assert store.append_jobs(_jobs(), scheduled_at=NOW) == 5
    assert store.append_jobs(_jobs(), scheduled_at=NOW + timedelta(minutes=1)) == 0

    records = store.list_jobs()
    assert len(records) == 5
    assert {item.status for item in records} == {ForwardJobStatus.PENDING}
    assert all(item.result_id is None for item in records)


def test_failed_job_persists_only_bounded_redacted_error(tmp_path) -> None:
    store = SQLiteForwardEvaluationStore(tmp_path / "research.db")
    job = _jobs()[0]
    store.append_jobs((job,), scheduled_at=NOW)

    store.mark_failed(
        job.job_id,
        error=RuntimeError("Authorization: Bearer secret-token " + "x" * 400),
        updated_at=NOW,
    )

    record = store.get_job(job.job_id)
    assert record.status is ForwardJobStatus.FAILED
    assert record.error_code == "RuntimeError"
    assert record.error_message is not None
    assert "secret-token" not in record.error_message
    assert len(record.error_message) <= 240
    assert record.result_id is None


def test_unavailable_job_has_reason_and_never_has_result(tmp_path) -> None:
    store = SQLiteForwardEvaluationStore(tmp_path / "research.db")
    job = _jobs()[0]
    store.append_jobs((job,), scheduled_at=NOW)

    store.mark_unavailable(
        job.job_id,
        reason=UnavailableReason.TARGET_CANDLE_NOT_AVAILABLE,
        updated_at=NOW,
    )

    record = store.get_job(job.job_id)
    assert record.status is ForwardJobStatus.UNAVAILABLE
    assert record.unavailable_reason is UnavailableReason.TARGET_CANDLE_NOT_AVAILABLE
    assert record.result_id is None


def test_completed_result_replays_exact_ordered_snapshot_without_provider(tmp_path) -> None:
    store = SQLiteForwardEvaluationStore(tmp_path / "research.db")
    job = _jobs()[0]
    store.append_jobs((job,), scheduled_at=NOW)
    snapshot = MarketSnapshot(
        (
            _candle(job.anchor_at),
            _candle(job.anchor_at + timedelta(minutes=1)),
            _candle(job.target_at),
        )
    )
    result = _result(job.job_id, snapshot)

    assert store.complete(job.job_id, snapshot=snapshot, result=result)
    assert not store.complete(job.job_id, snapshot=snapshot, result=result)

    persisted = store.get_result(result.result_id)
    replay_evidence = store.get_snapshot(persisted.snapshot_id)
    assert persisted == result
    assert replay_evidence == snapshot
    assert store.get_job(job.job_id).status is ForwardJobStatus.COMPLETED
    assert len(store.list_results(signal_id=signal().signal_id)) == 1


def test_market_evidence_and_results_reject_update_and_delete(tmp_path) -> None:
    database = tmp_path / "research.db"
    store = SQLiteForwardEvaluationStore(database)
    job = _jobs()[0]
    store.append_jobs((job,), scheduled_at=NOW)
    snapshot = MarketSnapshot((_candle(job.anchor_at), _candle(job.target_at)))
    result = _result(job.job_id, snapshot)
    store.complete(job.job_id, snapshot=snapshot, result=result)

    with sqlite3.connect(database) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "UPDATE research_forward_results SET target_return_bps = '0'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute("DELETE FROM research_market_candles")


def test_same_timestamp_correction_is_a_separate_persisted_revision(tmp_path) -> None:
    database = tmp_path / "research.db"
    store = SQLiteForwardEvaluationStore(database)
    jobs = _jobs()[:2]
    store.append_jobs(jobs, scheduled_at=NOW)
    first = _candle(jobs[0].anchor_at, close="150.05")
    corrected = _candle(jobs[0].anchor_at, close="150.06")
    first_snapshot = MarketSnapshot((first, _candle(jobs[0].target_at)))
    corrected_snapshot = MarketSnapshot((corrected, _candle(jobs[1].target_at)))

    store.complete(
        jobs[0].job_id,
        snapshot=first_snapshot,
        result=_result(jobs[0].job_id, first_snapshot),
    )
    store.complete(
        jobs[1].job_id,
        snapshot=corrected_snapshot,
        result=_result(jobs[1].job_id, corrected_snapshot),
    )

    with sqlite3.connect(database) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM research_market_candles WHERE open_time = ?",
            (jobs[0].anchor_at.isoformat(),),
        ).fetchone()
    assert count == (2,)


def test_cross_horizon_result_attachment_is_rejected_without_writes(tmp_path) -> None:
    store = SQLiteForwardEvaluationStore(tmp_path / "research.db")
    first_job, second_job = _jobs()[:2]
    store.append_jobs((first_job, second_job), scheduled_at=NOW)
    snapshot = MarketSnapshot(
        (_candle(second_job.anchor_at), _candle(second_job.target_at))
    )
    result = _result(second_job.job_id, snapshot)

    with pytest.raises(ValueError, match="persisted job"):
        store.complete(first_job.job_id, snapshot=snapshot, result=result)

    assert not store.list_results()
    assert store.get_job(first_job.job_id).status is ForwardJobStatus.PENDING


def test_snapshot_semantic_mismatch_is_rejected_without_writes(tmp_path) -> None:
    store = SQLiteForwardEvaluationStore(tmp_path / "research.db")
    job = _jobs()[0]
    store.append_jobs((job,), scheduled_at=NOW)
    mismatched = replace(_candle(job.anchor_at), source="another-market-source")
    snapshot = MarketSnapshot((mismatched, replace(mismatched, open_time=job.target_at)))
    result = replace(_result(job.job_id, snapshot), market_source=job.market_source)

    with pytest.raises(ValueError, match="Snapshot semantics"):
        store.complete(job.job_id, snapshot=snapshot, result=result)

    assert not store.list_results()
    assert store.get_job(job.job_id).status is ForwardJobStatus.PENDING


def test_existing_research_migrations_are_applied_without_modification(tmp_path) -> None:
    database = tmp_path / "research.db"
    SQLiteForwardEvaluationStore(database)

    with sqlite3.connect(database) as connection:
        versions = {
            row[0]
            for row in connection.execute("SELECT version FROM research_schema_migrations")
        }

    assert versions == {
        "0001_ingestion_state.sql",
        "0002_fetch_run_stage.sql",
        "0003_forward_signal_evaluation.sql",
    }
