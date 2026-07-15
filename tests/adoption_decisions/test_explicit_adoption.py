import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest
from swap_bot.adoption import (
    AdoptionFailureReason,
    AdoptionRejected,
    ResearchValidationEvidenceSnapshot,
    StrictCohortIdentity,
    approval_decision,
)
from swap_bot.adoption_application import ApproveSignalAdoptionOnceService
from swap_bot.adoption_store import SQLiteAdoptionStore
from swap_bot.research_evidence import SQLiteResearchValidationEvidenceSource

from tests.adoption_factories import NOW, adoption_policy, cohort_payload, seed_research_evidence


def test_dry_run_creates_no_live_database(tmp_path: Path) -> None:
    research = tmp_path / "research.sqlite3"
    live = tmp_path / "live.sqlite3"
    seed_research_evidence(research)

    result = ApproveSignalAdoptionOnceService(
        SQLiteResearchValidationEvidenceSource(research), clock=lambda: NOW
    ).run(
        assessment_id="assessment-validated-1",
        policy=adoption_policy(),
        approved_by="reviewer@example.com",
        reason="reviewed evidence",
    )

    assert result.would_approve
    assert not result.persisted
    assert not live.exists()


def test_explicit_apply_is_atomic_append_only_and_idempotent(tmp_path: Path) -> None:
    research = tmp_path / "research.sqlite3"
    live = tmp_path / "live.sqlite3"
    seed_research_evidence(research)
    store = SQLiteAdoptionStore(live)
    service = ApproveSignalAdoptionOnceService(
        SQLiteResearchValidationEvidenceSource(research), clock=lambda: NOW
    )
    request = {
        "assessment_id": "assessment-validated-1",
        "policy": adoption_policy(),
        "approved_by": "reviewer@example.com",
        "reason": "reviewed evidence",
        "apply": True,
        "store": store,
    }

    first = service.run(**request)  # type: ignore[arg-type]
    second = service.run(**request)  # type: ignore[arg-type]

    assert first.persisted and not first.reused
    assert not second.persisted and second.reused
    assert store.count_rows("live_research_validation_evidence_snapshots") == 1
    assert store.count_rows("live_strategy_adoption_policies") == 1
    assert store.count_rows("live_strategy_adoption_decisions") == 1
    with sqlite3.connect(live) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "UPDATE live_strategy_adoption_decisions SET reason = 'changed'"
            )


@pytest.mark.parametrize(
    "cohort_change",
    [
        {"scorer_version": "other-scorer"},
        {"model_version": "other-model"},
        {"prompt_version": "other-prompt"},
        {"signal_horizon": "1d"},
        {"forward_horizon": "4h"},
        {
            "market_source": "oanda-v20",
            "market_data_version": "oanda-mid-v1",
            "price_basis": "midpoint",
        },
        {"producer_version": "producer-v1"},
    ],
)
def test_adoption_requires_the_exact_research_cohort(
    tmp_path: Path, cohort_change: dict[str, object]
) -> None:
    research = tmp_path / "research.sqlite3"
    seed_research_evidence(research)
    changed = cohort_payload(**cohort_change)
    if cohort_change == {"producer_version": "producer-v1"}:
        changed["producer_version"] = None
    policy = adoption_policy(
        expected_cohort=StrictCohortIdentity.from_payload(changed)
    )

    with pytest.raises(AdoptionRejected) as rejected:
        ApproveSignalAdoptionOnceService(
            SQLiteResearchValidationEvidenceSource(research), clock=lambda: NOW
        ).run(
            assessment_id="assessment-validated-1",
            policy=policy,
            approved_by="reviewer@example.com",
            reason="reviewed evidence",
        )

    assert rejected.value.reason is AdoptionFailureReason.ADOPTION_POLICY_MISMATCH


def test_conflicting_policy_rolls_back_a_new_evidence_snapshot(tmp_path: Path) -> None:
    research = tmp_path / "research.sqlite3"
    live = tmp_path / "live.sqlite3"
    seed_research_evidence(research)
    source = SQLiteResearchValidationEvidenceSource(research)
    snapshot = ResearchValidationEvidenceSnapshot.from_evidence(
        source.read("assessment-validated-1"), imported_at=NOW
    )
    first_policy = adoption_policy()
    store = SQLiteAdoptionStore(live)
    store.apply_approval(
        snapshot,
        first_policy,
        approval_decision(
            snapshot,
            first_policy,
            decided_at=NOW,
            actor="reviewer@example.com",
            reason="first",
        ),
    )
    conflicting_policy = replace(first_policy, strategy_version="strategy-v2")
    second_snapshot = replace(
        snapshot,
        evidence_snapshot_id="research-evidence-second",
        assessment_id="assessment-second",
    )

    with pytest.raises(ValueError, match="policy version"):
        store.apply_approval(
            second_snapshot,
            conflicting_policy,
            approval_decision(
                second_snapshot,
                conflicting_policy,
                decided_at=NOW,
                actor="reviewer@example.com",
                reason="conflicting",
            ),
        )

    assert store.count_rows("live_research_validation_evidence_snapshots") == 1
    assert store.count_rows("live_strategy_adoption_decisions") == 1


def test_policy_requires_a_bounded_period() -> None:
    with pytest.raises(ValueError, match="after effective_from"):
        adoption_policy(expires_at=adoption_policy().effective_from)
