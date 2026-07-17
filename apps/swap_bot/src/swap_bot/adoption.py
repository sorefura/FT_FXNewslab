import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from fx_core import CurrencyTarget, Horizon, PairTarget, Signal
from fx_core.identity import canonical_json as _canonical_json
from fx_core.identity import digest as _digest
from fx_core.time import require_utc

RESEARCH_EVIDENCE_CONTRACT_VERSION = "research-validation-evidence-v1"
SUPPORTED_EVALUATION_SNAPSHOT_VERSION = "evaluation-input-snapshot-v2"


class ResearchValidationStatus(StrEnum):
    EXPERIMENTAL = "EXPERIMENTAL"
    PROMISING = "PROMISING"
    VALIDATED_FOR_RESEARCH = "VALIDATED_FOR_RESEARCH"


class AdoptionMode(StrEnum):
    SHADOW_ONLY = "SHADOW_ONLY"
    LIVE_ELIGIBLE = "LIVE_ELIGIBLE"


class RuntimeMode(StrEnum):
    SHADOW = "SHADOW"
    LIVE = "LIVE"


class AdoptionDecisionType(StrEnum):
    APPROVED_FOR_STRATEGY = "APPROVED_FOR_STRATEGY"
    REVOKED = "REVOKED"


class AdoptionFailureReason(StrEnum):
    RESEARCH_EVIDENCE_NOT_FOUND = "RESEARCH_EVIDENCE_NOT_FOUND"
    RESEARCH_STATUS_NOT_VALIDATED = "RESEARCH_STATUS_NOT_VALIDATED"
    RESEARCH_LINEAGE_INVALID = "RESEARCH_LINEAGE_INVALID"
    RESEARCH_CONTRACT_UNSUPPORTED = "RESEARCH_CONTRACT_UNSUPPORTED"
    ADOPTION_POLICY_MISMATCH = "ADOPTION_POLICY_MISMATCH"
    NO_ACTIVE_ADOPTION = "NO_ACTIVE_ADOPTION"
    ADOPTION_NOT_YET_EFFECTIVE = "ADOPTION_NOT_YET_EFFECTIVE"
    ADOPTION_EXPIRED = "ADOPTION_EXPIRED"
    ADOPTION_REVOKED = "ADOPTION_REVOKED"
    ADOPTION_MODE_NOT_ALLOWED = "ADOPTION_MODE_NOT_ALLOWED"
    SIGNAL_SPECIFICATION_MISMATCH = "SIGNAL_SPECIFICATION_MISMATCH"
    AMBIGUOUS_ADOPTION = "AMBIGUOUS_ADOPTION"


class AdoptionRejected(ValueError):
    def __init__(
        self,
        reason: AdoptionFailureReason,
        detail: str,
        *,
        context: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(detail)
        self.reason = reason
        self.context = dict(context or {})


@dataclass(frozen=True, slots=True)
class StrictCohortIdentity:
    signal_type: str
    target_type: str
    target_value: str
    signal_horizon: str
    forward_horizon: str
    producer_version: str | None
    model_version: str | None
    prompt_version: str | None
    scorer_version: str
    transformation_version: str | None
    market_source: str
    market_data_version: str
    price_basis: str
    granularity: str
    projection_version: str
    formula_version: str
    score_definition_version: str

    def __post_init__(self) -> None:
        for value, label in (
            (self.signal_type, "signal_type"),
            (self.target_type, "target_type"),
            (self.target_value, "target_value"),
            (self.signal_horizon, "signal_horizon"),
            (self.forward_horizon, "forward_horizon"),
            (self.scorer_version, "scorer_version"),
            (self.market_source, "market_source"),
            (self.market_data_version, "market_data_version"),
            (self.price_basis, "price_basis"),
            (self.granularity, "granularity"),
            (self.projection_version, "projection_version"),
            (self.formula_version, "formula_version"),
            (self.score_definition_version, "score_definition_version"),
        ):
            _require_text(value, label)
        for optional_value, label in (
            (self.producer_version, "producer_version"),
            (self.model_version, "model_version"),
            (self.prompt_version, "prompt_version"),
            (self.transformation_version, "transformation_version"),
        ):
            _optional_text(optional_value, label)
        if self.target_type not in {"currency", "pair"}:
            raise ValueError("target_type must be currency or pair")
        Horizon(self.signal_horizon)
        Horizon(self.forward_horizon)

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> "StrictCohortIdentity":
        required = {
            "signal_type",
            "target_type",
            "target_value",
            "signal_horizon",
            "forward_horizon",
            "producer_version",
            "model_version",
            "prompt_version",
            "scorer_version",
            "transformation_version",
            "market_source",
            "market_data_version",
            "price_basis",
            "granularity",
            "projection_version",
            "formula_version",
            "score_definition_version",
        }
        if set(payload) != required:
            raise ValueError("strict cohort fields do not match the supported contract")
        return cls(
            signal_type=_text(payload["signal_type"]),
            target_type=_text(payload["target_type"]),
            target_value=_text(payload["target_value"]),
            signal_horizon=_text(payload["signal_horizon"]),
            forward_horizon=_text(payload["forward_horizon"]),
            producer_version=_nullable_text(payload["producer_version"]),
            model_version=_nullable_text(payload["model_version"]),
            prompt_version=_nullable_text(payload["prompt_version"]),
            scorer_version=_text(payload["scorer_version"]),
            transformation_version=_nullable_text(payload["transformation_version"]),
            market_source=_text(payload["market_source"]),
            market_data_version=_text(payload["market_data_version"]),
            price_basis=_text(payload["price_basis"]),
            granularity=_text(payload["granularity"]),
            projection_version=_text(payload["projection_version"]),
            formula_version=_text(payload["formula_version"]),
            score_definition_version=_text(payload["score_definition_version"]),
        )

    @property
    def payload(self) -> dict[str, object]:
        return {
            "signal_type": self.signal_type,
            "target_type": self.target_type,
            "target_value": self.target_value,
            "signal_horizon": self.signal_horizon,
            "forward_horizon": self.forward_horizon,
            "producer_version": self.producer_version,
            "model_version": self.model_version,
            "prompt_version": self.prompt_version,
            "scorer_version": self.scorer_version,
            "transformation_version": self.transformation_version,
            "market_source": self.market_source,
            "market_data_version": self.market_data_version,
            "price_basis": self.price_basis,
            "granularity": self.granularity,
            "projection_version": self.projection_version,
            "formula_version": self.formula_version,
            "score_definition_version": self.score_definition_version,
        }

    @property
    def content_hash(self) -> str:
        return digest(self.payload)

    def matches_signal(self, signal: Signal) -> bool:
        if isinstance(signal.target, CurrencyTarget):
            target_type = "currency"
            target_value = signal.target.currency.code
        elif isinstance(signal.target, PairTarget):
            target_type = "pair"
            target_value = signal.target.pair.symbol
        else:
            return False
        versions = signal.versions
        return (
            signal.signal_type == self.signal_type
            and target_type == self.target_type
            and target_value == self.target_value
            and signal.horizon.value == self.signal_horizon
            and versions.producer_version == self.producer_version
            and versions.model_version == self.model_version
            and versions.prompt_version == self.prompt_version
            and versions.scorer_version == self.scorer_version
            and versions.transformation_version == self.transformation_version
        )


@dataclass(frozen=True, slots=True)
class ResearchValidationEvidence:
    assessment_id: str
    status: ResearchValidationStatus
    evaluation_run_id: str
    report_id: str
    research_policy_version: str
    research_policy_content_hash: str
    research_policy_payload: Mapping[str, Any]
    condition_results_payload: object
    cohort: StrictCohortIdentity
    metrics_payload: Mapping[str, Any]
    input_snapshot_version: str
    input_snapshot_identity_hash: str
    input_snapshot_payload: Mapping[str, Any]
    assessment_created_at: datetime
    report_created_at: datetime
    run_created_at: datetime
    research_policy_created_at: datetime

    def __post_init__(self) -> None:
        for value, label in (
            (self.assessment_id, "assessment_id"),
            (self.evaluation_run_id, "evaluation_run_id"),
            (self.report_id, "report_id"),
            (self.research_policy_version, "research_policy_version"),
            (self.research_policy_content_hash, "research_policy_content_hash"),
            (self.input_snapshot_version, "input_snapshot_version"),
            (self.input_snapshot_identity_hash, "input_snapshot_identity_hash"),
        ):
            _require_text(value, label)
        for timestamp, label in (
            (self.assessment_created_at, "assessment_created_at"),
            (self.report_created_at, "report_created_at"),
            (self.run_created_at, "run_created_at"),
            (self.research_policy_created_at, "research_policy_created_at"),
        ):
            require_utc(timestamp, label)


@dataclass(frozen=True, slots=True)
class ResearchValidationEvidenceSnapshot:
    evidence_snapshot_id: str
    source_contract_version: str
    assessment_id: str
    evaluation_run_id: str
    report_id: str
    research_policy_version: str
    research_policy_content_hash: str
    status: ResearchValidationStatus
    cohort_identity_payload: Mapping[str, Any]
    cohort_identity_hash: str
    metric_payload: Mapping[str, Any]
    metric_payload_hash: str
    condition_results_payload: object
    input_snapshot_version: str
    input_snapshot_identity_hash: str
    input_snapshot_payload: Mapping[str, Any]
    research_policy_payload: Mapping[str, Any]
    assessment_created_at: datetime
    report_created_at: datetime
    run_created_at: datetime
    research_policy_created_at: datetime
    imported_at: datetime

    def __post_init__(self) -> None:
        for timestamp in (
            self.assessment_created_at,
            self.report_created_at,
            self.run_created_at,
            self.research_policy_created_at,
            self.imported_at,
        ):
            require_utc(timestamp, "evidence snapshot timestamp")

    @property
    def identity_payload(self) -> dict[str, object]:
        return evidence_snapshot_identity_payload(
            source_contract_version=self.source_contract_version,
            assessment_id=self.assessment_id,
            evaluation_run_id=self.evaluation_run_id,
            report_id=self.report_id,
            research_policy_version=self.research_policy_version,
            research_policy_content_hash=self.research_policy_content_hash,
            status=self.status,
            cohort_identity_hash=self.cohort_identity_hash,
            metric_payload_hash=self.metric_payload_hash,
            condition_results_payload=self.condition_results_payload,
            input_snapshot_version=self.input_snapshot_version,
            input_snapshot_identity_hash=self.input_snapshot_identity_hash,
        )

    @property
    def expected_evidence_snapshot_id(self) -> str:
        return "research-evidence-" + digest(self.identity_payload)

    def validate_intrinsic_integrity(self) -> None:
        for value, label in (
            (self.evidence_snapshot_id, "evidence_snapshot_id"),
            (self.assessment_id, "assessment_id"),
            (self.evaluation_run_id, "evaluation_run_id"),
            (self.report_id, "report_id"),
            (self.research_policy_version, "research_policy_version"),
            (self.research_policy_content_hash, "research_policy_content_hash"),
            (self.input_snapshot_identity_hash, "input_snapshot_identity_hash"),
        ):
            _require_text(value, label)
        if self.source_contract_version != RESEARCH_EVIDENCE_CONTRACT_VERSION:
            raise ValueError("Research evidence contract version is not supported")
        if self.input_snapshot_version != SUPPORTED_EVALUATION_SNAPSHOT_VERSION:
            raise ValueError("Research input snapshot version is not supported")
        if not isinstance(self.status, ResearchValidationStatus):
            raise ValueError("Research validation status is invalid")
        for timestamp in (
            self.assessment_created_at,
            self.report_created_at,
            self.run_created_at,
            self.research_policy_created_at,
            self.imported_at,
        ):
            require_utc(timestamp, "evidence snapshot timestamp")
        if self.cohort_identity_hash != digest(self.cohort_identity_payload):
            raise ValueError("Research evidence cohort content hash does not match")
        if self.metric_payload_hash != digest(self.metric_payload):
            raise ValueError("Research evidence metric content hash does not match")
        if self.research_policy_content_hash != digest(
            self.research_policy_payload
        ):
            raise ValueError("Research evidence policy content hash does not match")
        if self.input_snapshot_identity_hash != digest(self.input_snapshot_payload):
            raise ValueError("Research input snapshot content hash does not match")
        if self.input_snapshot_payload.get("version") != self.input_snapshot_version:
            raise ValueError("Research input snapshot payload version does not match")
        if self.evidence_snapshot_id != self.expected_evidence_snapshot_id:
            raise ValueError("Research evidence snapshot identity does not match")

    @classmethod
    def from_evidence(
        cls, evidence: ResearchValidationEvidence, *, imported_at: datetime
    ) -> "ResearchValidationEvidenceSnapshot":
        require_utc(imported_at, "evidence imported_at")
        cohort_payload = _frozen_mapping(evidence.cohort.payload)
        metric_payload = _frozen_mapping(evidence.metrics_payload)
        snapshot_payload = _frozen_mapping(evidence.input_snapshot_payload)
        policy_payload = _frozen_mapping(evidence.research_policy_payload)
        condition_results_payload = _freeze_json(evidence.condition_results_payload)
        cohort_identity_hash = digest(cohort_payload)
        metric_payload_hash = digest(metric_payload)
        identity = evidence_snapshot_identity_payload(
            source_contract_version=RESEARCH_EVIDENCE_CONTRACT_VERSION,
            assessment_id=evidence.assessment_id,
            evaluation_run_id=evidence.evaluation_run_id,
            report_id=evidence.report_id,
            research_policy_version=evidence.research_policy_version,
            research_policy_content_hash=evidence.research_policy_content_hash,
            status=evidence.status,
            cohort_identity_hash=cohort_identity_hash,
            metric_payload_hash=metric_payload_hash,
            condition_results_payload=condition_results_payload,
            input_snapshot_version=evidence.input_snapshot_version,
            input_snapshot_identity_hash=evidence.input_snapshot_identity_hash,
        )
        return cls(
            evidence_snapshot_id="research-evidence-" + digest(identity),
            source_contract_version=RESEARCH_EVIDENCE_CONTRACT_VERSION,
            assessment_id=evidence.assessment_id,
            evaluation_run_id=evidence.evaluation_run_id,
            report_id=evidence.report_id,
            research_policy_version=evidence.research_policy_version,
            research_policy_content_hash=evidence.research_policy_content_hash,
            status=evidence.status,
            cohort_identity_payload=cohort_payload,
            cohort_identity_hash=cohort_identity_hash,
            metric_payload=metric_payload,
            metric_payload_hash=metric_payload_hash,
            condition_results_payload=condition_results_payload,
            input_snapshot_version=evidence.input_snapshot_version,
            input_snapshot_identity_hash=evidence.input_snapshot_identity_hash,
            input_snapshot_payload=snapshot_payload,
            research_policy_payload=policy_payload,
            assessment_created_at=evidence.assessment_created_at,
            report_created_at=evidence.report_created_at,
            run_created_at=evidence.run_created_at,
            research_policy_created_at=evidence.research_policy_created_at,
            imported_at=imported_at,
        )


def evidence_snapshot_identity_payload(
    *,
    source_contract_version: str,
    assessment_id: str,
    evaluation_run_id: str,
    report_id: str,
    research_policy_version: str,
    research_policy_content_hash: str,
    status: ResearchValidationStatus,
    cohort_identity_hash: str,
    metric_payload_hash: str,
    condition_results_payload: object,
    input_snapshot_version: str,
    input_snapshot_identity_hash: str,
) -> dict[str, object]:
    return {
        "source_contract_version": source_contract_version,
        "assessment_id": assessment_id,
        "evaluation_run_id": evaluation_run_id,
        "report_id": report_id,
        "research_policy_version": research_policy_version,
        "research_policy_content_hash": research_policy_content_hash,
        "status": status.value,
        "cohort_identity_hash": cohort_identity_hash,
        "metric_payload_hash": metric_payload_hash,
        "condition_results_hash": digest(condition_results_payload),
        "input_snapshot_version": input_snapshot_version,
        "input_snapshot_identity_hash": input_snapshot_identity_hash,
    }


@dataclass(frozen=True, slots=True)
class StrategyAdoptionPolicy:
    adoption_policy_version: str
    strategy_id: str
    strategy_version: str
    strategy_config_identity: str | None
    expected_research_policy_version: str
    expected_cohort: StrictCohortIdentity
    adoption_mode: AdoptionMode
    effective_from: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        for value, label in (
            (self.adoption_policy_version, "adoption_policy_version"),
            (self.strategy_id, "strategy_id"),
            (self.strategy_version, "strategy_version"),
            (self.expected_research_policy_version, "expected_research_policy_version"),
        ):
            _require_text(value, label)
        _optional_text(self.strategy_config_identity, "strategy_config_identity")
        require_utc(self.effective_from, "adoption effective_from")
        require_utc(self.expires_at, "adoption expires_at")
        if self.expires_at <= self.effective_from:
            raise ValueError("expires_at must be after effective_from")

    @classmethod
    def from_mapping(cls, payload: Mapping[str, object]) -> "StrategyAdoptionPolicy":
        required = {
            "adoption_policy_version",
            "strategy_id",
            "strategy_version",
            "strategy_config_identity",
            "expected_research_policy_version",
            "expected_exact_cohort_identity",
            "adoption_mode",
            "effective_from",
            "expires_at",
        }
        if set(payload) != required:
            raise ValueError("adoption policy fields do not match the required contract")
        cohort_payload = payload["expected_exact_cohort_identity"]
        if not isinstance(cohort_payload, dict):
            raise ValueError("expected_exact_cohort_identity must be an object")
        return cls(
            adoption_policy_version=_text(payload["adoption_policy_version"]),
            strategy_id=_text(payload["strategy_id"]),
            strategy_version=_text(payload["strategy_version"]),
            strategy_config_identity=_nullable_text(payload["strategy_config_identity"]),
            expected_research_policy_version=_text(
                payload["expected_research_policy_version"]
            ),
            expected_cohort=StrictCohortIdentity.from_payload(cohort_payload),
            adoption_mode=AdoptionMode(_text(payload["adoption_mode"])),
            effective_from=_datetime(payload["effective_from"], "effective_from"),
            expires_at=_datetime(payload["expires_at"], "expires_at"),
        )

    @property
    def identity_payload(self) -> dict[str, object]:
        return {
            "adoption_policy_version": self.adoption_policy_version,
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "strategy_config_identity": self.strategy_config_identity,
            "expected_research_policy_version": self.expected_research_policy_version,
            "expected_exact_cohort_identity": self.expected_cohort.payload,
            "adoption_mode": self.adoption_mode.value,
            "effective_from": self.effective_from.isoformat(),
            "expires_at": self.expires_at.isoformat(),
        }

    @property
    def content_hash(self) -> str:
        return digest(self.identity_payload)


@dataclass(frozen=True, slots=True)
class StrategyAdoptionDecision:
    adoption_decision_id: str
    decision_type: AdoptionDecisionType
    evidence_snapshot_id: str
    adoption_policy_version: str
    adoption_policy_content_hash: str
    strategy_id: str
    strategy_version: str
    strategy_config_identity: str | None
    approved_signal_specification: StrictCohortIdentity
    adoption_mode: AdoptionMode
    effective_from: datetime
    expires_at: datetime
    decided_at: datetime
    actor: str
    reason: str
    approval_decision_id: str | None = None

    def __post_init__(self) -> None:
        for value, label in (
            (self.adoption_decision_id, "adoption_decision_id"),
            (self.evidence_snapshot_id, "evidence_snapshot_id"),
            (self.adoption_policy_version, "adoption_policy_version"),
            (self.adoption_policy_content_hash, "adoption_policy_content_hash"),
            (self.strategy_id, "strategy_id"),
            (self.strategy_version, "strategy_version"),
            (self.actor, "actor"),
            (self.reason, "reason"),
        ):
            _require_text(value, label)
        require_utc(self.effective_from, "decision effective_from")
        require_utc(self.expires_at, "decision expires_at")
        require_utc(self.decided_at, "decision decided_at")
        if self.decision_type is AdoptionDecisionType.REVOKED:
            _require_text(self.approval_decision_id, "approval_decision_id")
        elif self.approval_decision_id is not None:
            raise ValueError("approval decision cannot reference another approval")

    @property
    def authority_payload(self) -> dict[str, object]:
        return {
            "decision_type": self.decision_type.value,
            "evidence_snapshot_id": self.evidence_snapshot_id,
            "adoption_policy_version": self.adoption_policy_version,
            "adoption_policy_content_hash": self.adoption_policy_content_hash,
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "strategy_config_identity": self.strategy_config_identity,
            "approved_signal_specification": self.approved_signal_specification.payload,
            "adoption_mode": self.adoption_mode.value,
            "effective_from": self.effective_from.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "approval_decision_id": self.approval_decision_id,
        }


@dataclass(frozen=True, slots=True)
class SignalAuthorization:
    authorization_id: str
    signal_id: str
    adoption_decision_id: str
    evidence_snapshot_id: str
    adoption_policy_version: str
    strategy_id: str
    strategy_version: str
    adoption_mode: AdoptionMode
    runtime_mode: RuntimeMode
    authorized_at: datetime

    def __post_init__(self) -> None:
        for value in (
            self.authorization_id,
            self.signal_id,
            self.adoption_decision_id,
            self.evidence_snapshot_id,
            self.adoption_policy_version,
            self.strategy_id,
            self.strategy_version,
        ):
            _require_text(value, "Signal authorization identity")
        require_utc(self.authorized_at, "authorization authorized_at")


@dataclass(frozen=True, slots=True)
class AuthorizedSignal:
    signal: Signal
    authorization: SignalAuthorization

    def __post_init__(self) -> None:
        if self.signal.signal_id.value != self.authorization.signal_id:
            raise ValueError("authorization belongs to another Signal")


def approval_decision(
    snapshot: ResearchValidationEvidenceSnapshot,
    policy: StrategyAdoptionPolicy,
    *,
    decided_at: datetime,
    actor: str,
    reason: str,
) -> StrategyAdoptionDecision:
    identity = {
        "decision_type": AdoptionDecisionType.APPROVED_FOR_STRATEGY.value,
        "evidence_snapshot_id": snapshot.evidence_snapshot_id,
        "policy_version": policy.adoption_policy_version,
        "policy_hash": policy.content_hash,
    }
    return StrategyAdoptionDecision(
        adoption_decision_id="adoption-approval-" + digest(identity),
        decision_type=AdoptionDecisionType.APPROVED_FOR_STRATEGY,
        evidence_snapshot_id=snapshot.evidence_snapshot_id,
        adoption_policy_version=policy.adoption_policy_version,
        adoption_policy_content_hash=policy.content_hash,
        strategy_id=policy.strategy_id,
        strategy_version=policy.strategy_version,
        strategy_config_identity=policy.strategy_config_identity,
        approved_signal_specification=policy.expected_cohort,
        adoption_mode=policy.adoption_mode,
        effective_from=policy.effective_from,
        expires_at=policy.expires_at,
        decided_at=decided_at,
        actor=actor,
        reason=reason,
    )


def adoption_authority_start(
    effective_from: datetime, decided_at: datetime
) -> datetime:
    require_utc(effective_from, "adoption effective_from")
    require_utc(decided_at, "adoption decided_at")
    return max(effective_from, decided_at)


def revocation_decision(
    approval: StrategyAdoptionDecision,
    *,
    decided_at: datetime,
    actor: str,
    reason: str,
) -> StrategyAdoptionDecision:
    if approval.decision_type is not AdoptionDecisionType.APPROVED_FOR_STRATEGY:
        raise ValueError("only an approval can be revoked")
    identity = {
        "decision_type": AdoptionDecisionType.REVOKED.value,
        "approval_decision_id": approval.adoption_decision_id,
    }
    return StrategyAdoptionDecision(
        adoption_decision_id="adoption-revocation-" + digest(identity),
        decision_type=AdoptionDecisionType.REVOKED,
        evidence_snapshot_id=approval.evidence_snapshot_id,
        adoption_policy_version=approval.adoption_policy_version,
        adoption_policy_content_hash=approval.adoption_policy_content_hash,
        strategy_id=approval.strategy_id,
        strategy_version=approval.strategy_version,
        strategy_config_identity=approval.strategy_config_identity,
        approved_signal_specification=approval.approved_signal_specification,
        adoption_mode=approval.adoption_mode,
        effective_from=approval.effective_from,
        expires_at=approval.expires_at,
        decided_at=decided_at,
        actor=actor,
        reason=reason,
        approval_decision_id=approval.adoption_decision_id,
    )


def canonical_json(payload: object) -> str:
    return _canonical_json(payload)


def digest(payload: object) -> str:
    return _digest(payload)


def _require_text(value: str | None, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must not be blank")


def _optional_text(value: str | None, label: str) -> None:
    if value is not None:
        _require_text(value, label)


def _text(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("value must be text")
    return value


def _nullable_text(value: object) -> str | None:
    return None if value is None else _text(value)


def _datetime(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise TypeError(f"{label} must be an ISO datetime string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require_utc(parsed, label)
    return parsed


def _freeze_json(value: object) -> object:
    return _immutable_json(json.loads(canonical_json(value)))


def _frozen_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    copied = json.loads(canonical_json(value))
    if not isinstance(copied, dict):
        raise TypeError("evidence payload must be an object")
    frozen = _immutable_json(copied)
    if not isinstance(frozen, Mapping):
        raise TypeError("evidence payload must remain an object")
    return frozen


def _immutable_json(value: object) -> object:
    if isinstance(value, dict):
        return MappingProxyType(
            {str(key): _immutable_json(item) for key, item in value.items()}
        )
    if isinstance(value, list):
        return tuple(_immutable_json(item) for item in value)
    return value
