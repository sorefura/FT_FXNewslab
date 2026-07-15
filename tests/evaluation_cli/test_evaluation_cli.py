import json
import sys
from pathlib import Path

import pytest
from fx_research.__main__ import main
from fx_research.evaluation_application import EvaluateSignalsOnceResult

from tests.evaluation_integration_helpers import seed_evaluation_database


def test_evaluate_signals_once_cli_emits_json_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database = tmp_path / "research.db"
    seed_evaluation_database(database)
    monkeypatch.setattr(
        sys,
        "argv",
        ["fx_research", "evaluate-signals-once", "--database", str(database)],
    )

    exit_code = main()

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload == {
        "assessments_created": 0,
        "cohort_count": 1,
        "completed_forward_results_scanned": 1,
        "evaluation_sample_count": 1,
        "failed": 0,
        "incomplete_horizon_signal_count": 0,
        "insufficient_sample_count": 2,
        "reports_created": 1,
        "reports_reused": 0,
        "undefined_metric_count": 2,
        "unsupported_signal_count": 0,
    }


def test_validation_policy_file_is_required_to_create_assessment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database = tmp_path / "research.db"
    seed_evaluation_database(database)
    policy = tmp_path / "policy.json"
    policy.write_text(
        json.dumps(
            {
                "policy_version": "cli-policy-v1",
                "minimum_sample_count": 1,
                "minimum_spearman": 0.0,
                "minimum_spearman_ci_lower": None,
                "minimum_hit_rate": 0.0,
                "minimum_hit_rate_ci_lower": None,
                "required_non_empty_bucket_count": 1,
                "minimum_adjacent_step_ratio": 0.0,
                "stability_slice_minimum_count": 1,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "fx_research",
            "evaluate-signals-once",
            "--database",
            str(database),
            "--validation-policy",
            str(policy),
        ],
    )

    assert main() == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["assessments_created"] == 1


def test_cli_returns_nonzero_when_evaluation_reports_processing_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database = tmp_path / "research.db"

    monkeypatch.setattr(
        "fx_research.__main__.EvaluateSignalsOnceService.run",
        lambda self, policy: EvaluateSignalsOnceResult(
            completed_forward_results_scanned=1,
            evaluation_sample_count=0,
            unsupported_signal_count=0,
            incomplete_horizon_signal_count=0,
            cohort_count=1,
            reports_created=0,
            reports_reused=0,
            assessments_created=0,
            undefined_metric_count=0,
            insufficient_sample_count=0,
            failed=1,
        ),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["fx_research", "evaluate-signals-once", "--database", str(database)],
    )

    exit_code = main()

    assert exit_code == 1
    assert json.loads(capsys.readouterr().out)["failed"] == 1
