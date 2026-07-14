import sqlite3
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
)
from fx_research.evaluation_application import EvaluateSignalsOnceService
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

    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        store.append_assessment(assessment)


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
