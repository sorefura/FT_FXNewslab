import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from .adoption import (
    AdoptionDecisionType,
    AdoptionFailureReason,
    AdoptionRejected,
    ResearchValidationEvidenceSnapshot,
    ResearchValidationStatus,
    StrategyAdoptionDecision,
    StrategyAdoptionPolicy,
    approval_decision,
    revocation_decision,
)
from .adoption_store import SQLiteAdoptionStore
from .research_evidence import ResearchValidationEvidenceSource


class AdoptionDecisionSource(Protocol):
    def get_decision(self, decision_id: str) -> StrategyAdoptionDecision: ...


@dataclass(frozen=True, slots=True)
class AdoptionApprovalResult:
    assessment_found: bool
    research_status: str
    research_lineage_valid: bool
    evidence_snapshot_id: str
    policy_match: bool
    adoption_mode: str
    would_approve: bool
    persisted: bool
    reused: bool
    adoption_decision_id: str
    failure_reasons: tuple[str, ...] = ()

    def summary(self) -> dict[str, object]:
        return {
            "assessment_found": self.assessment_found,
            "research_status": self.research_status,
            "research_lineage_valid": self.research_lineage_valid,
            "evidence_snapshot_id": self.evidence_snapshot_id,
            "policy_match": self.policy_match,
            "adoption_mode": self.adoption_mode,
            "would_approve": self.would_approve,
            "persisted": self.persisted,
            "reused": self.reused,
            "adoption_decision_id": self.adoption_decision_id,
            "failure_reasons": list(self.failure_reasons),
        }


@dataclass(frozen=True, slots=True)
class AdoptionRevocationResult:
    approval_decision_id: str
    revocation_decision_id: str
    would_revoke: bool
    persisted: bool
    reused: bool

    def summary(self) -> dict[str, object]:
        return {
            "approval_decision_id": self.approval_decision_id,
            "revocation_decision_id": self.revocation_decision_id,
            "would_revoke": self.would_revoke,
            "persisted": self.persisted,
            "reused": self.reused,
        }


class ApproveSignalAdoptionOnceService:
    def __init__(
        self,
        evidence_source: ResearchValidationEvidenceSource,
        *,
        clock: Callable[[], datetime],
    ) -> None:
        self._evidence_source = evidence_source
        self._clock = clock

    def run(
        self,
        *,
        assessment_id: str,
        policy: StrategyAdoptionPolicy,
        approved_by: str,
        reason: str,
        apply: bool = False,
        store: SQLiteAdoptionStore | None = None,
    ) -> AdoptionApprovalResult:
        evidence = self._evidence_source.read(assessment_id)
        if evidence.status is not ResearchValidationStatus.VALIDATED_FOR_RESEARCH:
            raise AdoptionRejected(
                AdoptionFailureReason.RESEARCH_STATUS_NOT_VALIDATED,
                f"Research status is {evidence.status.value}",
            )
        if (
            evidence.research_policy_version
            != policy.expected_research_policy_version
            or evidence.cohort != policy.expected_cohort
        ):
            raise AdoptionRejected(
                AdoptionFailureReason.ADOPTION_POLICY_MISMATCH,
                "Research evidence does not match the exact adoption policy",
            )
        now = self._clock()
        if now >= policy.expires_at:
            raise AdoptionRejected(
                AdoptionFailureReason.ADOPTION_EXPIRED,
                "adoption policy is already expired",
            )
        snapshot = ResearchValidationEvidenceSnapshot.from_evidence(
            evidence, imported_at=now
        )
        decision = approval_decision(
            snapshot,
            policy,
            decided_at=now,
            actor=approved_by,
            reason=reason,
        )
        persisted = False
        reused = False
        if apply:
            if store is None:
                raise ValueError("an adoption store is required for --apply")
            result = store.apply_approval(snapshot, policy, decision)
            persisted = result.decision_created
            reused = result.reused
        return AdoptionApprovalResult(
            assessment_found=True,
            research_status=evidence.status.value,
            research_lineage_valid=True,
            evidence_snapshot_id=snapshot.evidence_snapshot_id,
            policy_match=True,
            adoption_mode=policy.adoption_mode.value,
            would_approve=True,
            persisted=persisted,
            reused=reused,
            adoption_decision_id=decision.adoption_decision_id,
        )


class RevokeSignalAdoptionOnceService:
    def __init__(
        self,
        decision_source: AdoptionDecisionSource,
        *,
        clock: Callable[[], datetime],
    ) -> None:
        self._decision_source = decision_source
        self._clock = clock

    def run(
        self,
        *,
        approval_decision_id: str,
        revoked_by: str,
        reason: str,
        apply: bool = False,
        store: SQLiteAdoptionStore | None = None,
    ) -> AdoptionRevocationResult:
        approval = self._decision_source.get_decision(approval_decision_id)
        if approval.decision_type is not AdoptionDecisionType.APPROVED_FOR_STRATEGY:
            raise ValueError("the referenced decision is not an approval")
        revocation = revocation_decision(
            approval,
            decided_at=self._clock(),
            actor=revoked_by,
            reason=reason,
        )
        persisted = False
        reused = False
        if apply:
            if store is None:
                raise ValueError("an adoption store is required for --apply")
            persisted = store.append_revocation(revocation)
            reused = not persisted
        return AdoptionRevocationResult(
            approval_decision_id=approval_decision_id,
            revocation_decision_id=revocation.adoption_decision_id,
            would_revoke=True,
            persisted=persisted,
            reused=reused,
        )


def adoption_policy_from_file(path: str | Path) -> StrategyAdoptionPolicy:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("adoption policy JSON must be an object")
    return StrategyAdoptionPolicy.from_mapping(payload)
