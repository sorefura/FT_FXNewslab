import hashlib
from pathlib import Path

import pytest
from swap_bot.adoption import AdoptionFailureReason, AdoptionRejected
from swap_bot.adoption_application import ApproveSignalAdoptionOnceService
from swap_bot.research_evidence import SQLiteResearchValidationEvidenceSource

from tests.adoption_factories import NOW, adoption_policy, seed_research_evidence


def test_exact_validated_assessment_is_read_without_changing_research_database(
    tmp_path: Path,
) -> None:
    database = tmp_path / "research.sqlite3"
    seed_research_evidence(database)
    before = hashlib.sha256(database.read_bytes()).hexdigest()

    evidence = SQLiteResearchValidationEvidenceSource(database).read(
        "assessment-validated-1"
    )

    assert evidence.status.value == "VALIDATED_FOR_RESEARCH"
    assert evidence.report_id == "evaluation-report-1"
    assert evidence.input_snapshot_version == "evaluation-input-snapshot-v2"
    assert evidence.cohort.market_source == "gmo-fx-public-v1"
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before


@pytest.mark.parametrize("status", ["EXPERIMENTAL", "PROMISING"])
def test_research_status_without_validation_cannot_be_approved(
    tmp_path: Path, status: str
) -> None:
    database = tmp_path / "research.sqlite3"
    seed_research_evidence(database, status=status)
    service = ApproveSignalAdoptionOnceService(
        SQLiteResearchValidationEvidenceSource(database), clock=lambda: NOW
    )

    with pytest.raises(AdoptionRejected) as rejected:
        service.run(
            assessment_id="assessment-validated-1",
            policy=adoption_policy(),
            approved_by="reviewer@example.com",
            reason="reviewed evidence",
        )

    assert rejected.value.reason is AdoptionFailureReason.RESEARCH_STATUS_NOT_VALIDATED


def test_missing_assessment_fails_closed(tmp_path: Path) -> None:
    database = tmp_path / "research.sqlite3"
    seed_research_evidence(database)

    with pytest.raises(AdoptionRejected) as rejected:
        SQLiteResearchValidationEvidenceSource(database).read("missing")

    assert rejected.value.reason is AdoptionFailureReason.RESEARCH_EVIDENCE_NOT_FOUND


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        (
            {"report_run_id": "evaluation-run-report"},
            AdoptionFailureReason.RESEARCH_LINEAGE_INVALID,
        ),
        (
            {"malformed": "policy_hash"},
            AdoptionFailureReason.RESEARCH_LINEAGE_INVALID,
        ),
        (
            {"include_snapshot": False},
            AdoptionFailureReason.RESEARCH_LINEAGE_INVALID,
        ),
        (
            {"snapshot_version": "evaluation-input-snapshot-v99"},
            AdoptionFailureReason.RESEARCH_CONTRACT_UNSUPPORTED,
        ),
        (
            {"malformed": "cohort"},
            AdoptionFailureReason.RESEARCH_LINEAGE_INVALID,
        ),
        (
            {"malformed": "metrics"},
            AdoptionFailureReason.RESEARCH_LINEAGE_INVALID,
        ),
        (
            {"malformed": "conditions"},
            AdoptionFailureReason.RESEARCH_LINEAGE_INVALID,
        ),
    ],
)
def test_incomplete_or_malformed_research_lineage_fails_closed(
    tmp_path: Path,
    changes: dict[str, object],
    expected: AdoptionFailureReason,
) -> None:
    database = tmp_path / "research.sqlite3"
    seed_research_evidence(database, **changes)  # type: ignore[arg-type]

    with pytest.raises(AdoptionRejected) as rejected:
        SQLiteResearchValidationEvidenceSource(database).read(
            "assessment-validated-1"
        )

    assert rejected.value.reason is expected
