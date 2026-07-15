import sqlite3
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fx_research.evaluation import (
    BootstrapConfiguration,
    EvaluationConfiguration,
    MetricConfiguration,
    ValidationAssessment,
    ValidationPolicy,
    ValidationStatus,
    group_samples,
)
from fx_research.evaluation_application import (
    EvaluateSignalsOnceService,
    assess_evaluation,
)
from fx_research.evaluation_metrics import evaluate_cohort
from fx_research.evaluation_persistence import SQLiteEvaluationStore

from tests.evaluation_integration_helpers import seed_evaluation_database

NOW = datetime(2026, 7, 21, tzinfo=UTC)
CONFIGURATION = EvaluationConfiguration(
    metric=MetricConfiguration(),
    bootstrap=BootstrapConfiguration(seed=11, iterations=100),
)


def _policy() -> ValidationPolicy:
    return ValidationPolicy(
        policy_version="permissive-research-policy-v1",
        minimum_sample_count=5,
        minimum_spearman=0.5,
        minimum_spearman_ci_lower=None,
        minimum_hit_rate=0.5,
        minimum_hit_rate_ci_lower=None,
        required_non_empty_bucket_count=5,
        minimum_adjacent_step_ratio=1.0,
        stability_slice_minimum_count=5,
    )


def test_report_only_run_does_not_create_validation_assessment(tmp_path: Path) -> None:
    database = tmp_path / "research.db"
    seed_evaluation_database(database)

    result = EvaluateSignalsOnceService(
        SQLiteEvaluationStore(database),
        clock=lambda: NOW,
        configuration=CONFIGURATION,
    ).run()

    assert result.completed_forward_results_scanned == 1
    assert result.evaluation_sample_count == 1
    assert result.cohort_count == 1
    assert result.reports_created == 1
    assert result.assessments_created == 0
    assert result.failed == 0
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM research_validation_assessments"
        ).fetchone() == (0,)


def test_repeated_run_reuses_report_and_does_not_duplicate_assessment(
    tmp_path: Path,
) -> None:
    database = tmp_path / "research.db"
    seed_evaluation_database(
        database,
        ((-0.8, "-4"), (-0.4, "-2"), (0.0, "1"), (0.4, "2"), (0.8, "4")),
    )
    service = EvaluateSignalsOnceService(
        SQLiteEvaluationStore(database),
        clock=lambda: NOW,
        configuration=CONFIGURATION,
    )

    first = service.run(_policy())
    second = service.run(_policy())

    assert first.reports_created == 1
    assert first.assessments_created == 1
    assert second.reports_created == 0
    assert second.reports_reused == 1
    assert second.assessments_created == 0
    with sqlite3.connect(database) as connection:
        status = connection.execute(
            "SELECT status FROM research_validation_assessments"
        ).fetchone()
    assert status == (ValidationStatus.VALIDATED_FOR_RESEARCH.value,)


def test_assessment_rejects_report_from_another_evaluation_run(tmp_path: Path) -> None:
    database = tmp_path / "research.db"
    seed_evaluation_database(database)
    store = SQLiteEvaluationStore(database)
    snapshot = store.capture_inputs()
    evaluation = _evaluation(snapshot)
    first = store.append_run(
        snapshot,
        (evaluation,),
        CONFIGURATION,
        created_at=NOW,
    )
    second = store.append_run(
        snapshot,
        (evaluation,),
        replace(CONFIGURATION, evaluator_version="signal-validation-review-v1"),
        created_at=NOW,
    )
    policy = _policy()
    store.append_policy(policy, created_at=NOW)
    assessment = assess_evaluation(
        second.run.run_id,
        first.run.reports[0],
        evaluation,
        policy,
        created_at=NOW,
    )

    with pytest.raises(ValueError, match="belongs to another run"):
        store.append_assessment(assessment, evaluation)

    _assert_no_assessment(database)


def test_assessment_rejects_policy_version_with_wrong_content_hash(
    tmp_path: Path,
) -> None:
    database = tmp_path / "research.db"
    seed_evaluation_database(database)
    store = SQLiteEvaluationStore(database)
    snapshot = store.capture_inputs()
    evaluation = _evaluation(snapshot)
    appended = store.append_run(
        snapshot,
        (evaluation,),
        CONFIGURATION,
        created_at=NOW,
    )
    policy = _policy()
    store.append_policy(policy, created_at=NOW)
    assessment = replace(
        assess_evaluation(
            appended.run.run_id,
            appended.run.reports[0],
            evaluation,
            policy,
            created_at=NOW,
        ),
        policy_content_hash="different-policy-content-hash",
    )

    with pytest.raises(ValueError, match="content hash differs"):
        store.append_assessment(assessment, evaluation)

    _assert_no_assessment(database)


@pytest.mark.parametrize("mismatch", ["cohort", "metrics"])
def test_reused_run_rejects_recomputed_report_payload_mismatch_without_assessment(
    tmp_path: Path,
    mismatch: str,
) -> None:
    database = tmp_path / "research.db"
    seed_evaluation_database(database)
    store = SQLiteEvaluationStore(database)
    snapshot = store.capture_inputs()
    evaluation = _evaluation(snapshot)
    first = store.append_run(
        snapshot,
        (evaluation,),
        CONFIGURATION,
        created_at=NOW,
    )
    reused = store.append_run(
        snapshot,
        (evaluation,),
        CONFIGURATION,
        created_at=NOW,
    )
    assert not reused.created
    policy = _policy()
    store.append_policy(policy, created_at=NOW)
    if mismatch == "cohort":
        recomputed = replace(
            evaluation,
            cohort=replace(evaluation.cohort, price_basis="midpoint"),
        )
    else:
        recomputed = replace(
            evaluation,
            metrics=replace(
                evaluation.metrics,
                spearman=replace(
                    evaluation.metrics.spearman,
                    sample_count=evaluation.metrics.spearman.sample_count + 1,
                ),
            ),
        )
    assessment = assess_evaluation(
        reused.run.run_id,
        first.run.reports[0],
        recomputed,
        policy,
        created_at=NOW,
    )

    with pytest.raises(ValueError, match=f"Evaluation {mismatch}"):
        store.append_assessment(assessment, recomputed)

    _assert_no_assessment(database)


def test_assessment_cannot_be_saved_before_its_policy(tmp_path: Path) -> None:
    database = tmp_path / "research.db"
    seed_evaluation_database(database)
    store = SQLiteEvaluationStore(database)
    run_result = EvaluateSignalsOnceService(
        store,
        clock=lambda: NOW,
        configuration=CONFIGURATION,
    ).run()
    assert run_result.reports_created == 1
    run = store.capture_inputs()
    assert run.samples
    with sqlite3.connect(database) as connection:
        run_id, report_id = connection.execute(
            "SELECT run_id, report_id FROM research_evaluation_reports"
        ).fetchone()
    assessment = ValidationAssessment(
        assessment_id="assessment-without-policy",
        evaluation_run_id=run_id,
        report_id=report_id,
        policy_version="missing-policy-v1",
        policy_content_hash="missing-policy-hash",
        status=ValidationStatus.EXPERIMENTAL,
        condition_results=(("minimum_sample_count", False),),
        created_at=NOW,
    )

    _, samples = group_samples(run.samples)[0]
    evaluation = evaluate_cohort(
        samples,
        (),
        metric_configuration=CONFIGURATION.metric,
        bootstrap_configuration=CONFIGURATION.bootstrap,
    )
    with pytest.raises(ValueError, match="policy does not exist"):
        store.append_assessment(assessment, evaluation)


def test_assessment_status_has_no_strategy_approval_value() -> None:
    assert {item.value for item in ValidationStatus} == {
        "EXPERIMENTAL",
        "PROMISING",
        "VALIDATED_FOR_RESEARCH",
    }
    with pytest.raises(ValueError):
        ValidationStatus("APPROVED_FOR_STRATEGY")


def test_metric_failure_is_nonzero_application_failure_without_partial_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "research.db"
    seed_evaluation_database(database)

    def fail_evaluation(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise ValueError("synthetic metric failure")

    monkeypatch.setattr(
        "fx_research.evaluation_application.evaluate_cohort", fail_evaluation
    )
    result = EvaluateSignalsOnceService(
        SQLiteEvaluationStore(database),
        clock=lambda: NOW,
        configuration=CONFIGURATION,
    ).run()

    assert result.failed == 1
    assert result.reports_created == 0
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM research_evaluation_runs"
        ).fetchone() == (0,)


def _evaluation(snapshot):  # type: ignore[no-untyped-def]
    cohort, samples = group_samples(snapshot.samples)[0]
    exclusions = tuple(
        item.reason
        for item in snapshot.exclusions
        if item.cohort.cohort_id == cohort.cohort_id
    )
    return evaluate_cohort(
        samples,
        exclusions,
        metric_configuration=CONFIGURATION.metric,
        bootstrap_configuration=CONFIGURATION.bootstrap,
    )


def _assert_no_assessment(database: Path) -> None:
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM research_validation_assessments"
        ).fetchone() == (0,)
