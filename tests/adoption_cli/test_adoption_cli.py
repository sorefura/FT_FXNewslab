import json
import sqlite3
import sys
from pathlib import Path

import pytest
from swap_bot.__main__ import main
from swap_bot.adoption import StrategyAdoptionPolicy

from tests.adoption_factories import cohort_payload, seed_research_evidence


def _policy_file(path: Path) -> Path:
    policy = {
        "adoption_policy_version": "adoption-policy-cli-v1",
        "strategy_id": "validated-signal-shadow",
        "strategy_version": "strategy-v1",
        "strategy_config_identity": "config-sha256-1",
        "expected_research_policy_version": "research-policy-v1",
        "expected_exact_cohort_identity": cohort_payload(),
        "adoption_mode": "SHADOW_ONLY",
        "effective_from": "2026-07-01T00:00:00+00:00",
        "expires_at": "2027-07-01T00:00:00+00:00",
    }
    path.write_text(json.dumps(policy), encoding="utf-8")
    return path


def test_approval_cli_defaults_to_dry_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    research = tmp_path / "research.sqlite3"
    live = tmp_path / "live.sqlite3"
    seed_research_evidence(research)
    policy = _policy_file(tmp_path / "policy.json")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "swap_bot",
            "approve-signal-adoption-once",
            "--research-database",
            str(research),
            "--live-database",
            str(live),
            "--assessment-id",
            "assessment-validated-1",
            "--policy",
            str(policy),
            "--approved-by",
            "reviewer@example.com",
            "--reason",
            "reviewed evidence",
        ],
    )

    assert main() == 0

    summary = json.loads(capsys.readouterr().out)
    assert summary["would_approve"]
    assert not summary["persisted"]
    assert not live.exists()


def test_approval_and_revocation_cli_require_explicit_apply(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    research = tmp_path / "research.sqlite3"
    live = tmp_path / "live.sqlite3"
    seed_research_evidence(research)
    policy = _policy_file(tmp_path / "policy.json")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "swap_bot",
            "approve-signal-adoption-once",
            "--research-database",
            str(research),
            "--live-database",
            str(live),
            "--assessment-id",
            "assessment-validated-1",
            "--policy",
            str(policy),
            "--approved-by",
            "reviewer@example.com",
            "--reason",
            "reviewed evidence",
            "--apply",
        ],
    )
    assert main() == 0
    approval = json.loads(capsys.readouterr().out)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "swap_bot",
            "revoke-signal-adoption-once",
            "--live-database",
            str(live),
            "--approval-decision-id",
            approval["adoption_decision_id"],
            "--revoked-by",
            "reviewer@example.com",
            "--reason",
            "superseded",
        ],
    )
    assert main() == 0
    dry_run = json.loads(capsys.readouterr().out)
    assert dry_run["would_revoke"] and not dry_run["persisted"]
    with sqlite3.connect(live) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM live_strategy_adoption_decisions"
        ).fetchone()[0] == 1

    monkeypatch.setattr(sys, "argv", [*sys.argv, "--apply"])
    assert main() == 0
    applied = json.loads(capsys.readouterr().out)
    assert applied["persisted"]
    with sqlite3.connect(live) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM live_strategy_adoption_decisions"
        ).fetchone()[0] == 2


def test_policy_without_expiration_is_rejected() -> None:
    payload = {
        "adoption_policy_version": "policy-v1",
        "strategy_id": "strategy",
        "strategy_version": "v1",
        "strategy_config_identity": None,
        "expected_research_policy_version": "research-policy-v1",
        "expected_exact_cohort_identity": cohort_payload(),
        "adoption_mode": "SHADOW_ONLY",
        "effective_from": "2026-07-01T00:00:00+00:00",
    }

    with pytest.raises(ValueError, match="fields"):
        StrategyAdoptionPolicy.from_mapping(payload)
