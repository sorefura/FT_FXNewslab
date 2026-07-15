import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fx_core import Currency, CurrencyPair, CurrencyTarget
from fx_research.evaluation import (
    BootstrapConfiguration,
    EvaluationConfiguration,
    EvaluationExclusionReason,
    MetricConfiguration,
    ValidationPolicy,
    group_samples,
)
from fx_research.evaluation_metrics import evaluate_cohort
from fx_research.evaluation_persistence import SQLiteEvaluationStore
from fx_research.forward import (
    ForwardObservationJob,
    ForwardResult,
    MarketCandle,
    MarketSnapshot,
    UnavailableReason,
)
from fx_research.forward_persistence import SQLiteForwardEvaluationStore
from fx_signal_store import SQLiteSignalStore

from tests.factories import feature, observation, signal

NOW = datetime(2026, 7, 20, tzinfo=UTC)
CONFIGURATION = EvaluationConfiguration(
    metric=MetricConfiguration(),
    bootstrap=BootstrapConfiguration(seed=5, iterations=20),
)


def _database_with_signal(path: Path) -> tuple[SQLiteSignalStore, SQLiteForwardEvaluationStore]:
    signal_store = SQLiteSignalStore(path)
    signal_store.append_observation(observation())
    signal_store.append_feature(feature())
    signal_store.append_signal(signal())
    forward_store = SQLiteForwardEvaluationStore(path)
    from fx_research.forward import schedule_forward_jobs

    jobs = schedule_forward_jobs(
        signal(),
        market_source="gmo-fx-public-v1",
        market_data_version="gmo-fx-kline-bid-v1",
        price_basis="bid",
    )
    forward_store.append_jobs(jobs, scheduled_at=NOW)
    return signal_store, forward_store


def _complete(
    forward_store: SQLiteForwardEvaluationStore,
    job: ForwardObservationJob,
    *,
    suffix: str,
) -> ForwardResult:
    first = _candle(job.anchor_at, "150")
    last = _candle(job.target_at, "151")
    snapshot = MarketSnapshot((first, last))
    result = ForwardResult(
        result_id=f"evaluation-result-{suffix}",
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
        target_return_bps=Decimal("66.6666666667"),
        mfe_bps=Decimal("80"),
        mae_bps=Decimal("-20"),
        realized_volatility=0.01,
        completed_at=NOW + timedelta(minutes=1),
        market_source=job.market_source,
        market_data_version=job.market_data_version,
        price_basis=job.price_basis,
        granularity=job.granularity,
        formula_version=job.formula_version,
        snapshot_id=snapshot.snapshot_id,
    )
    forward_store.complete(job.job_id, snapshot=snapshot, result=result)
    return result


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


def _evaluations(snapshot):  # type: ignore[no-untyped-def]
    exclusions_by_cohort: dict[str, list[EvaluationExclusionReason]] = {}
    for item in snapshot.exclusions:
        exclusions_by_cohort.setdefault(item.cohort.cohort_id, []).append(item.reason)
    return tuple(
        evaluate_cohort(
            samples,
            tuple(exclusions_by_cohort.get(cohort.cohort_id, ())),
            metric_configuration=CONFIGURATION.metric,
            bootstrap_configuration=CONFIGURATION.bootstrap,
        )
        for cohort, samples in group_samples(snapshot.samples)
    )


def _append_signal_records(
    store: SQLiteSignalStore,
    *,
    signal_id: str,
    target: CurrencyTarget | None = None,
):  # type: ignore[no-untyped-def]
    observation_id = f"obs-{signal_id}"
    feature_id = f"feature-{signal_id}"
    store.append_observation(observation(observation_id))
    store.append_feature(feature(feature_id, observation_id))
    source_signal = signal(signal_id, feature_id)
    if target is not None:
        source_signal = replace(source_signal, target=target)
    store.append_signal(source_signal)
    return source_signal


def test_capture_includes_completed_results_and_excludes_non_completed_jobs(
    tmp_path: Path,
) -> None:
    _, forward_store = _database_with_signal(tmp_path / "research.db")
    jobs = forward_store.list_jobs()
    _complete(forward_store, jobs[0].job, suffix="first")
    forward_store.mark_failed(
        jobs[1].job.job_id, error=TimeoutError("provider failed"), updated_at=NOW
    )
    from fx_research.forward import UnavailableReason

    forward_store.mark_unavailable(
        jobs[2].job.job_id,
        reason=UnavailableReason.TARGET_CANDLE_NOT_AVAILABLE,
        updated_at=NOW,
    )

    snapshot = SQLiteEvaluationStore(tmp_path / "research.db").capture_inputs()

    assert snapshot.completed_results_scanned == 1
    assert len(snapshot.samples) == 1
    assert {item.reason for item in snapshot.exclusions} == {
        EvaluationExclusionReason.PENDING_FORWARD_JOB,
        EvaluationExclusionReason.FAILED_FORWARD_JOB,
        EvaluationExclusionReason.UNAVAILABLE_FORWARD_JOB,
    }
    assert snapshot.samples[0].target_return_bps != 0


def test_identical_ordered_inputs_and_configuration_reuse_evaluation_run(
    tmp_path: Path,
) -> None:
    database = tmp_path / "research.db"
    _, forward_store = _database_with_signal(database)
    _complete(forward_store, forward_store.list_jobs()[0].job, suffix="first")
    store = SQLiteEvaluationStore(database)
    snapshot = store.capture_inputs()
    evaluations = _evaluations(snapshot)

    first = store.append_run(snapshot, evaluations, CONFIGURATION, created_at=NOW)
    second = store.append_run(
        snapshot, evaluations, CONFIGURATION, created_at=NOW + timedelta(hours=1)
    )

    assert first.created
    assert not second.created
    assert first.run == second.run
    assert first.run.ordered_input_identity == snapshot.ordered_input_identity
    assert first.run.input_snapshot is not None
    assert first.run.input_snapshot.identity_payload == snapshot.identity_payload()
    assert len(first.run.reports) == 1


def test_forward_job_state_changes_create_new_full_snapshot_runs(tmp_path: Path) -> None:
    database = tmp_path / "research.db"
    _, forward_store = _database_with_signal(database)
    jobs = forward_store.list_jobs()
    _complete(forward_store, jobs[0].job, suffix="first")
    store = SQLiteEvaluationStore(database)

    pending_snapshot = store.capture_inputs()
    pending_run = store.append_run(
        pending_snapshot,
        _evaluations(pending_snapshot),
        CONFIGURATION,
        created_at=NOW,
    )
    forward_store.mark_failed(
        jobs[1].job.job_id,
        error=TimeoutError("provider failed"),
        updated_at=NOW + timedelta(minutes=2),
    )
    failed_snapshot = store.capture_inputs()
    failed_run = store.append_run(
        failed_snapshot,
        _evaluations(failed_snapshot),
        CONFIGURATION,
        created_at=NOW + timedelta(minutes=3),
    )
    forward_store.mark_unavailable(
        jobs[2].job.job_id,
        reason=UnavailableReason.TARGET_CANDLE_NOT_AVAILABLE,
        updated_at=NOW + timedelta(minutes=4),
    )
    unavailable_snapshot = store.capture_inputs()
    unavailable_run = store.append_run(
        unavailable_snapshot,
        _evaluations(unavailable_snapshot),
        CONFIGURATION,
        created_at=NOW + timedelta(minutes=5),
    )

    assert len(
        {
            pending_run.run.run_id,
            failed_run.run.run_id,
            unavailable_run.run.run_id,
        }
    ) == 3
    assert failed_run.run.input_snapshot is not None
    assert unavailable_run.run.input_snapshot is not None
    failed_statuses = {
        item["job_id"]: item["captured_status"]
        for item in failed_run.run.input_snapshot.identity_payload["exclusions"]
    }
    unavailable_statuses = {
        item["job_id"]: item["captured_status"]
        for item in unavailable_run.run.input_snapshot.identity_payload["exclusions"]
    }
    assert failed_statuses[jobs[1].job.job_id] == "FAILED"
    assert unavailable_statuses[jobs[2].job.job_id] == "UNAVAILABLE"


def test_snapshot_persists_exclusions_and_unsupported_or_incomplete_signals_as_evidence(
    tmp_path: Path,
) -> None:
    database = tmp_path / "research.db"
    signal_store, forward_store = _database_with_signal(database)
    _complete(forward_store, forward_store.list_jobs()[0].job, suffix="first")
    unsupported = _append_signal_records(
        signal_store,
        signal_id="signal-unsupported-eur",
        target=CurrencyTarget(Currency("EUR")),
    )
    incomplete = _append_signal_records(
        signal_store,
        signal_id="signal-incomplete-usd",
    )
    store = SQLiteEvaluationStore(database)

    snapshot = store.capture_inputs()
    appended = store.append_run(
        snapshot,
        _evaluations(snapshot),
        CONFIGURATION,
        created_at=NOW,
    )

    assert snapshot.unsupported_signal_ids == (unsupported.signal_id,)
    assert snapshot.incomplete_horizon_signal_ids == (incomplete.signal_id,)
    assert len(snapshot.samples) == 1
    assert len(snapshot.exclusions) == 4
    assert all(item.captured_status.value == "PENDING" for item in snapshot.exclusions)
    assert appended.run.input_snapshot is not None
    evidence = appended.run.input_snapshot.identity_payload
    assert evidence["unsupported_signal_ids"] == [unsupported.signal_id.value]
    assert evidence["incomplete_horizon_signal_ids"] == [incomplete.signal_id.value]
    assert len(evidence["completed_inputs"]) == 1
    assert len(evidence["exclusions"]) == 4


def test_new_completed_result_creates_new_run_without_changing_old_input_snapshot(
    tmp_path: Path,
) -> None:
    database = tmp_path / "research.db"
    _, forward_store = _database_with_signal(database)
    jobs = forward_store.list_jobs()
    first_result = _complete(forward_store, jobs[0].job, suffix="first")
    store = SQLiteEvaluationStore(database)
    first_snapshot = store.capture_inputs()
    first_run = store.append_run(
        first_snapshot,
        _evaluations(first_snapshot),
        CONFIGURATION,
        created_at=NOW,
    )

    second_result = _complete(forward_store, jobs[1].job, suffix="second")
    second_snapshot = store.capture_inputs()
    second_run = store.append_run(
        second_snapshot,
        _evaluations(second_snapshot),
        CONFIGURATION,
        created_at=NOW + timedelta(hours=1),
    )

    assert first_snapshot.ordered_input_identity == (
        (signal().signal_id.value, first_result.result_id),
    )
    assert second_snapshot.ordered_input_identity == tuple(
        sorted(
            (
                (signal().signal_id.value, first_result.result_id),
                (signal().signal_id.value, second_result.result_id),
            )
        )
    )
    assert first_run.run.run_id != second_run.run.run_id
    assert store.get_run(first_run.run.run_id).ordered_input_identity == (
        (signal().signal_id.value, first_result.result_id),
    )


def test_evaluation_reports_preserve_exact_cohort_and_metric_payloads(tmp_path: Path) -> None:
    database = tmp_path / "research.db"
    _, forward_store = _database_with_signal(database)
    _complete(forward_store, forward_store.list_jobs()[0].job, suffix="first")
    store = SQLiteEvaluationStore(database)
    snapshot = store.capture_inputs()

    appended = store.append_run(
        snapshot, _evaluations(snapshot), CONFIGURATION, created_at=NOW
    )

    report = appended.run.reports[0]
    assert report.cohort_identity["price_basis"] == "bid"
    assert report.cohort_identity["signal_horizon"] == "3d"
    assert report.cohort_identity["forward_horizon"] == "15m"
    assert report.metrics["spearman"]["sample_count"] == 1
    assert report.metrics["spearman"]["undefined_reason"] == "INSUFFICIENT_SAMPLE"


def test_report_rejects_sample_from_a_different_captured_cohort_without_partial_write(
    tmp_path: Path,
) -> None:
    database = tmp_path / "research.db"
    _, forward_store = _database_with_signal(database)
    _complete(forward_store, forward_store.list_jobs()[0].job, suffix="first")
    store = SQLiteEvaluationStore(database)
    snapshot = store.capture_inputs()
    evaluation = _evaluations(snapshot)[0]
    wrong_cohort = replace(evaluation.cohort, price_basis="midpoint")

    with pytest.raises(ValueError, match="captured sample cohort"):
        store.append_run(
            snapshot,
            (replace(evaluation, cohort=wrong_cohort),),
            CONFIGURATION,
            created_at=NOW,
        )

    _assert_no_run_or_report(database)


def test_input_cannot_appear_in_two_reports_without_partial_write(tmp_path: Path) -> None:
    database = tmp_path / "research.db"
    _, forward_store = _database_with_signal(database)
    _complete(forward_store, forward_store.list_jobs()[0].job, suffix="first")
    store = SQLiteEvaluationStore(database)
    snapshot = store.capture_inputs()
    evaluation = _evaluations(snapshot)[0]
    duplicate_report = replace(
        evaluation,
        cohort=replace(evaluation.cohort, price_basis="midpoint"),
    )

    with pytest.raises(ValueError, match="more than one report"):
        store.append_run(
            snapshot,
            (evaluation, duplicate_report),
            CONFIGURATION,
            created_at=NOW,
        )

    _assert_no_run_or_report(database)


def test_report_cannot_omit_a_captured_input_without_partial_write(tmp_path: Path) -> None:
    database = tmp_path / "research.db"
    _, forward_store = _database_with_signal(database)
    _complete(forward_store, forward_store.list_jobs()[0].job, suffix="first")
    store = SQLiteEvaluationStore(database)
    snapshot = store.capture_inputs()
    evaluation = replace(_evaluations(snapshot)[0], sample_input_ids=())

    with pytest.raises(ValueError, match="exact input snapshot"):
        store.append_run(
            snapshot,
            (evaluation,),
            CONFIGURATION,
            created_at=NOW,
        )

    _assert_no_run_or_report(database)


def test_evaluation_records_reject_update_and_delete(tmp_path: Path) -> None:
    database = tmp_path / "research.db"
    _, forward_store = _database_with_signal(database)
    _complete(forward_store, forward_store.list_jobs()[0].job, suffix="first")
    store = SQLiteEvaluationStore(database)
    snapshot = store.capture_inputs()
    appended = store.append_run(
        snapshot, _evaluations(snapshot), CONFIGURATION, created_at=NOW
    )

    with sqlite3.connect(database) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "UPDATE research_evaluation_runs SET evaluator_version = 'changed'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "DELETE FROM research_evaluation_reports WHERE report_id = ?",
                (appended.run.reports[0].report_id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "UPDATE research_evaluation_input_snapshots "
                "SET snapshot_version = 'changed'"
            )


def test_validation_policy_version_cannot_change_content(tmp_path: Path) -> None:
    database = tmp_path / "research.db"
    SQLiteSignalStore(database)
    store = SQLiteEvaluationStore(database)
    policy = ValidationPolicy(
        policy_version="research-policy-v1",
        minimum_sample_count=100,
        minimum_spearman=0.05,
        minimum_spearman_ci_lower=0.0,
        minimum_hit_rate=0.52,
        minimum_hit_rate_ci_lower=0.5,
        required_non_empty_bucket_count=4,
        minimum_adjacent_step_ratio=0.75,
        stability_slice_minimum_count=20,
    )

    assert store.append_policy(policy, created_at=NOW)
    assert not store.append_policy(policy, created_at=NOW + timedelta(minutes=1))
    with pytest.raises(ValueError, match="different content"):
        store.append_policy(
            replace(policy, minimum_sample_count=200),
            created_at=NOW + timedelta(minutes=2),
        )


def _assert_no_run_or_report(database: Path) -> None:
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM research_evaluation_runs"
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT COUNT(*) FROM research_evaluation_reports"
        ).fetchone() == (0,)
