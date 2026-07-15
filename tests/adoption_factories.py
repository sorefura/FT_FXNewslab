import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fx_research.evaluation_persistence import SQLiteEvaluationStore
from swap_bot.adoption import AdoptionMode, StrategyAdoptionPolicy, StrictCohortIdentity

NOW = datetime(2026, 7, 15, 3, 0, tzinfo=UTC)


def cohort_payload(**changes: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "signal_type": "currency_fundamental",
        "target_type": "currency",
        "target_value": "USD",
        "signal_horizon": "3d",
        "forward_horizon": "1d",
        "producer_version": "producer-v1",
        "model_version": "model-v1",
        "prompt_version": "prompt-v1",
        "scorer_version": "fundamental-scorer-v1",
        "transformation_version": None,
        "market_source": "gmo-fx-public-v1",
        "market_data_version": "gmo-fx-kline-bid-v1",
        "price_basis": "bid",
        "granularity": "M1",
        "projection_version": "currency-usdjpy-projection-v1",
        "formula_version": "forward-result-v1",
        "score_definition_version": "signal-direction-v1",
    }
    payload.update(changes)
    return payload


def adoption_policy(**changes: object) -> StrategyAdoptionPolicy:
    values: dict[str, object] = {
        "adoption_policy_version": "adoption-policy-v1",
        "strategy_id": "validated-signal-shadow",
        "strategy_version": "strategy-v1",
        "strategy_config_identity": "config-sha256-1",
        "expected_research_policy_version": "research-policy-v1",
        "expected_cohort": StrictCohortIdentity.from_payload(cohort_payload()),
        "adoption_mode": AdoptionMode.SHADOW_ONLY,
        "effective_from": NOW - timedelta(minutes=1),
        "expires_at": NOW + timedelta(days=30),
    }
    values.update(changes)
    return StrategyAdoptionPolicy(**values)  # type: ignore[arg-type]


def seed_research_evidence(
    path: Path,
    *,
    assessment_id: str = "assessment-validated-1",
    status: str = "VALIDATED_FOR_RESEARCH",
    report_run_id: str = "evaluation-run-1",
    assessment_run_id: str = "evaluation-run-1",
    snapshot_version: str = "evaluation-input-snapshot-v2",
    include_snapshot: bool = True,
    cohort: dict[str, object] | None = None,
    malformed: str | None = None,
) -> None:
    SQLiteEvaluationStore(path)
    cohort = cohort or cohort_payload()
    policy_payload = {
        "policy_version": "research-policy-v1",
        "minimum_sample_count": 10,
        "minimum_spearman": 0.1,
        "minimum_spearman_ci_lower": None,
        "minimum_hit_rate": 0.5,
        "minimum_hit_rate_ci_lower": None,
        "required_non_empty_bucket_count": 2,
        "minimum_adjacent_step_ratio": 0.5,
        "stability_slice_minimum_count": 2,
    }
    policy_json = _canonical(policy_payload)
    policy_hash = _digest(policy_payload)
    metrics: object = {"diagnostics": {"included_samples": 12}, "spearman": 0.4}
    conditions: object = [["minimum_sample_count", True], ["minimum_spearman", True]]
    snapshot: object = {
        "version": snapshot_version,
        "signals_scanned": 12,
        "completed_results_scanned": 12,
        "completed_inputs": [],
        "exclusions": [],
        "unsupported_signal_ids": [],
        "incomplete_horizon_signal_ids": [],
    }
    if malformed == "cohort":
        cohort_json = "{"
    else:
        cohort_json = _canonical(cohort)
    if malformed == "metrics":
        metrics_json = "[]"
    else:
        metrics_json = _canonical(metrics)
    if malformed == "conditions":
        condition_json = _canonical([["condition", "not-bool"]])
    else:
        condition_json = _canonical(conditions)
    if malformed == "snapshot":
        snapshot_json = "{"
        snapshot_hash = "malformed"
    else:
        snapshot_json = _canonical(snapshot)
        snapshot_hash = _digest(snapshot)
    cohort_id = "evaluation-cohort-" + _digest(cohort)
    timestamp = NOW.isoformat()
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        for run_id in {report_run_id, assessment_run_id}:
            connection.execute(
                "INSERT INTO research_evaluation_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    "signal-validation-v1",
                    "signal-direction-v1",
                    "strict-evaluation-cohort-v1",
                    "ordered-input-hash",
                    "{}",
                    "{}",
                    timestamp,
                ),
            )
        if include_snapshot:
            connection.execute(
                "INSERT INTO research_evaluation_input_snapshots VALUES (?, ?, ?, ?, ?, ?)",
                (
                    assessment_run_id,
                    snapshot_version,
                    snapshot_hash,
                    12,
                    12,
                    snapshot_json,
                ),
            )
        connection.execute(
            "INSERT INTO research_evaluation_reports VALUES (?, ?, ?, ?, ?, ?)",
            (
                "evaluation-report-1",
                report_run_id,
                cohort_id,
                cohort_json,
                metrics_json,
                timestamp,
            ),
        )
        connection.execute(
            "INSERT INTO research_validation_policies VALUES (?, ?, ?, ?)",
            ("research-policy-v1", policy_hash, policy_json, timestamp),
        )
        assessment_policy_hash = (
            "wrong-policy-hash" if malformed == "policy_hash" else policy_hash
        )
        connection.execute(
            "INSERT INTO research_validation_assessments VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                assessment_id,
                assessment_run_id,
                "evaluation-report-1",
                "research-policy-v1",
                assessment_policy_hash,
                status,
                condition_json,
                timestamp,
            ),
        )


def _canonical(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _digest(payload: object) -> str:
    return hashlib.sha256(_canonical(payload).encode()).hexdigest()
