from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from fx_core import (
    CurrencyPair,
    PairScore,
    PairTarget,
    Probability,
    SignalId,
)
from fx_core.time import require_utc

from ..adoption import AuthorizedSignal, digest
from ..models import PositionId, Side
from .swap_evidence import OperationalSwapEvidence
from .versions import (
    ENTRY_EVALUATION_CONTRACT_VERSION,
    POSITION_CLOSE_CANDIDATE_CONTRACT_VERSION,
    POSITION_EXIT_EVALUATION_CONTRACT_VERSION,
    PRODUCTION_CANDIDATE_CONTRACT_VERSION,
)


class EntryEvaluationOutcome(StrEnum):
    CANDIDATE = "CANDIDATE"
    SKIP = "SKIP"


class EntrySkipReason(StrEnum):
    SIGNAL_NOT_PAIR_TARGET = "SIGNAL_NOT_PAIR_TARGET"
    PAIR_NOT_CONFIGURED = "PAIR_NOT_CONFIGURED"
    SIGNAL_TYPE_MISMATCH = "SIGNAL_TYPE_MISMATCH"
    TRANSFORMATION_VERSION_MISMATCH = "TRANSFORMATION_VERSION_MISMATCH"
    SIGNAL_STRATEGY_IDENTITY_MISMATCH = "SIGNAL_STRATEGY_IDENTITY_MISMATCH"
    SIGNAL_CONFIG_IDENTITY_MISMATCH = "SIGNAL_CONFIG_IDENTITY_MISMATCH"
    SIGNAL_IN_FUTURE = "SIGNAL_IN_FUTURE"
    SIGNAL_STALE = "SIGNAL_STALE"
    DIRECTION_NEUTRAL = "DIRECTION_NEUTRAL"
    SWAP_WRONG_PAIR = "SWAP_WRONG_PAIR"
    SWAP_UNKNOWN = "SWAP_UNKNOWN"
    SWAP_UNAVAILABLE = "SWAP_UNAVAILABLE"
    SWAP_NOT_APPLICABLE = "SWAP_NOT_APPLICABLE"
    SWAP_STALE = "SWAP_STALE"
    SWAP_MALFORMED = "SWAP_MALFORMED"
    CARRY_NOT_POSITIVE = "CARRY_NOT_POSITIVE"
    DIRECTION_CARRY_MISMATCH = "DIRECTION_CARRY_MISMATCH"


class PositionExitReason(StrEnum):
    SIGNAL_REVERSED = "SIGNAL_REVERSED"
    CARRY_NO_LONGER_POSITIVE = "CARRY_NO_LONGER_POSITIVE"
    MAXIMUM_HOLDING_AGE = "MAXIMUM_HOLDING_AGE"
    ADOPTION_NO_LONGER_ACTIVE = "ADOPTION_NO_LONGER_ACTIVE"
    REQUIRED_SIGNAL_MISSING_OR_STALE = "REQUIRED_SIGNAL_MISSING_OR_STALE"
    REQUIRED_SWAP_MISSING_OR_STALE = "REQUIRED_SWAP_MISSING_OR_STALE"


class PositionExitEvaluationOutcome(StrEnum):
    CLOSE_CANDIDATE = "CLOSE_CANDIDATE"
    KEEP = "KEEP"


class PositionExitKeepReason(StrEnum):
    NO_EXIT_CONDITION = "NO_EXIT_CONDITION"


@dataclass(frozen=True, slots=True)
class ProductionEntryEvaluationInput:
    authorized_pair_signal: AuthorizedSignal
    approved_strategy_config_identity: str
    swap_evidence: OperationalSwapEvidence
    evaluated_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.authorized_pair_signal, AuthorizedSignal):
            raise TypeError("production entry requires an AuthorizedSignal")
        _require_text(
            self.approved_strategy_config_identity,
            "approved_strategy_config_identity",
        )
        self.swap_evidence.validate_intrinsic_integrity()
        require_utc(self.evaluated_at, "entry evaluated_at")


@dataclass(frozen=True, slots=True)
class ProductionTradeCandidate:
    candidate_id: str
    candidate_contract_version: str
    strategy_evaluation_id: str
    strategy_id: str
    strategy_version: str
    strategy_config_identity: str
    pair: CurrencyPair
    side: Side
    pair_score: PairScore
    confidence: Probability
    signal_id: SignalId
    authorization_id: str
    swap_evidence_id: str
    created_at: datetime

    def __post_init__(self) -> None:
        if self.candidate_contract_version != PRODUCTION_CANDIDATE_CONTRACT_VERSION:
            raise ValueError("unsupported ProductionTradeCandidate contract")
        for value, label in (
            (self.candidate_id, "candidate_id"),
            (self.strategy_evaluation_id, "strategy_evaluation_id"),
            (self.strategy_id, "strategy_id"),
            (self.strategy_version, "strategy_version"),
            (self.strategy_config_identity, "strategy_config_identity"),
            (self.authorization_id, "authorization_id"),
            (self.swap_evidence_id, "swap_evidence_id"),
        ):
            _require_text(value, label)
        require_utc(self.created_at, "production candidate created_at")
        if self.candidate_id != self.expected_candidate_id:
            raise ValueError("candidate_id does not match intrinsic candidate")

    @classmethod
    def create(
        cls,
        *,
        candidate_contract_version: str,
        strategy_evaluation_id: str,
        strategy_id: str,
        strategy_version: str,
        strategy_config_identity: str,
        pair: CurrencyPair,
        side: Side,
        pair_score: PairScore,
        confidence: Probability,
        signal_id: SignalId,
        authorization_id: str,
        swap_evidence_id: str,
        created_at: datetime,
    ) -> "ProductionTradeCandidate":
        values: dict[str, object] = {
            "candidate_contract_version": candidate_contract_version,
            "strategy_evaluation_id": strategy_evaluation_id,
            "strategy_id": strategy_id,
            "strategy_version": strategy_version,
            "strategy_config_identity": strategy_config_identity,
            "pair": pair.symbol,
            "side": side.value,
            "pair_score": pair_score.value,
            "confidence": confidence.value,
            "signal_id": signal_id.value,
            "authorization_id": authorization_id,
            "swap_evidence_id": swap_evidence_id,
            "created_at": created_at.isoformat(),
        }
        return cls(
            candidate_id="production-candidate-" + digest(values),
            candidate_contract_version=candidate_contract_version,
            strategy_evaluation_id=strategy_evaluation_id,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            strategy_config_identity=strategy_config_identity,
            pair=pair,
            side=side,
            pair_score=pair_score,
            confidence=confidence,
            signal_id=signal_id,
            authorization_id=authorization_id,
            swap_evidence_id=swap_evidence_id,
            created_at=created_at,
        )

    @property
    def evaluation_result_payload(self) -> dict[str, object]:
        return {
            "candidate_contract_version": self.candidate_contract_version,
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "strategy_config_identity": self.strategy_config_identity,
            "pair": self.pair.symbol,
            "side": self.side.value,
            "pair_score": self.pair_score.value,
            "confidence": self.confidence.value,
            "signal_id": self.signal_id.value,
            "authorization_id": self.authorization_id,
            "swap_evidence_id": self.swap_evidence_id,
            "created_at": self.created_at.isoformat(),
        }

    @property
    def identity_payload(self) -> dict[str, object]:
        return {
            "strategy_evaluation_id": self.strategy_evaluation_id,
            **self.evaluation_result_payload,
        }

    @property
    def expected_candidate_id(self) -> str:
        return "production-candidate-" + digest(self.identity_payload)


@dataclass(frozen=True, slots=True)
class ProductionEntryEvaluation:
    evaluation_id: str
    evaluation_contract_version: str
    strategy_id: str
    strategy_version: str
    strategy_config_identity: str
    pair: CurrencyPair
    signal_id: SignalId
    authorization_id: str
    adoption_decision_id: str
    adoption_policy_version: str
    swap_evidence_id: str
    evaluated_at: datetime
    outcome: EntryEvaluationOutcome
    candidate: ProductionTradeCandidate | None
    skip_reason: EntrySkipReason | None

    def __post_init__(self) -> None:
        if self.evaluation_contract_version != ENTRY_EVALUATION_CONTRACT_VERSION:
            raise ValueError("unsupported ProductionEntryEvaluation contract")
        for value, label in (
            (self.evaluation_id, "evaluation_id"),
            (self.strategy_id, "strategy_id"),
            (self.strategy_version, "strategy_version"),
            (self.strategy_config_identity, "strategy_config_identity"),
            (self.authorization_id, "authorization_id"),
            (self.adoption_decision_id, "adoption_decision_id"),
            (self.adoption_policy_version, "adoption_policy_version"),
            (self.swap_evidence_id, "swap_evidence_id"),
        ):
            _require_text(value, label)
        require_utc(self.evaluated_at, "entry evaluation evaluated_at")
        if self.outcome is EntryEvaluationOutcome.CANDIDATE:
            if self.candidate is None or self.skip_reason is not None:
                raise ValueError("CANDIDATE requires candidate and prohibits skip_reason")
            self._validate_candidate_lineage(self.candidate)
        elif self.candidate is not None or self.skip_reason is None:
            raise ValueError("SKIP requires skip_reason and prohibits candidate")
        if self.evaluation_id != self.expected_evaluation_id:
            raise ValueError("evaluation_id does not match intrinsic evaluation")

    @classmethod
    def create_candidate(
        cls,
        evaluation_input: ProductionEntryEvaluationInput,
        *,
        candidate_contract_version: str,
        side: Side,
    ) -> "ProductionEntryEvaluation":
        authorized = evaluation_input.authorized_pair_signal
        if not isinstance(authorized.signal.target, PairTarget) or not isinstance(
            authorized.signal.direction, PairScore
        ):
            raise TypeError("candidate result requires an authorized Pair Signal")
        pair = authorized.signal.target.pair
        if evaluation_input.swap_evidence.pair != pair:
            raise ValueError("candidate result requires swap evidence for the Signal Pair")
        common = _entry_common_payload(evaluation_input, pair)
        candidate_result: dict[str, object] = {
            "candidate_contract_version": candidate_contract_version,
            "strategy_id": authorized.authorization.strategy_id,
            "strategy_version": authorized.authorization.strategy_version,
            "strategy_config_identity": evaluation_input.approved_strategy_config_identity,
            "pair": pair.symbol,
            "side": side.value,
            "pair_score": authorized.signal.direction.value,
            "confidence": authorized.signal.confidence.value,
            "signal_id": authorized.signal.signal_id.value,
            "authorization_id": authorized.authorization.authorization_id,
            "swap_evidence_id": evaluation_input.swap_evidence.swap_evidence_id,
            "created_at": evaluation_input.evaluated_at.isoformat(),
        }
        evaluation_id = _entry_evaluation_id(
            common,
            outcome=EntryEvaluationOutcome.CANDIDATE,
            result=candidate_result,
        )
        candidate = ProductionTradeCandidate.create(
            candidate_contract_version=candidate_contract_version,
            strategy_evaluation_id=evaluation_id,
            strategy_id=authorized.authorization.strategy_id,
            strategy_version=authorized.authorization.strategy_version,
            strategy_config_identity=evaluation_input.approved_strategy_config_identity,
            pair=pair,
            side=side,
            pair_score=authorized.signal.direction,
            confidence=authorized.signal.confidence,
            signal_id=authorized.signal.signal_id,
            authorization_id=authorized.authorization.authorization_id,
            swap_evidence_id=evaluation_input.swap_evidence.swap_evidence_id,
            created_at=evaluation_input.evaluated_at,
        )
        return cls(
            evaluation_id=evaluation_id,
            evaluation_contract_version=ENTRY_EVALUATION_CONTRACT_VERSION,
            strategy_id=authorized.authorization.strategy_id,
            strategy_version=authorized.authorization.strategy_version,
            strategy_config_identity=evaluation_input.approved_strategy_config_identity,
            pair=pair,
            signal_id=authorized.signal.signal_id,
            authorization_id=authorized.authorization.authorization_id,
            adoption_decision_id=authorized.authorization.adoption_decision_id,
            adoption_policy_version=authorized.authorization.adoption_policy_version,
            swap_evidence_id=evaluation_input.swap_evidence.swap_evidence_id,
            evaluated_at=evaluation_input.evaluated_at,
            outcome=EntryEvaluationOutcome.CANDIDATE,
            candidate=candidate,
            skip_reason=None,
        )

    @classmethod
    def create_skip(
        cls,
        evaluation_input: ProductionEntryEvaluationInput,
        *,
        reason: EntrySkipReason,
    ) -> "ProductionEntryEvaluation":
        authorized = evaluation_input.authorized_pair_signal
        pair = evaluation_input.swap_evidence.pair
        common = _entry_common_payload(evaluation_input, pair)
        evaluation_id = _entry_evaluation_id(
            common,
            outcome=EntryEvaluationOutcome.SKIP,
            result={"skip_reason": reason.value},
        )
        return cls(
            evaluation_id=evaluation_id,
            evaluation_contract_version=ENTRY_EVALUATION_CONTRACT_VERSION,
            strategy_id=authorized.authorization.strategy_id,
            strategy_version=authorized.authorization.strategy_version,
            strategy_config_identity=evaluation_input.approved_strategy_config_identity,
            pair=pair,
            signal_id=authorized.signal.signal_id,
            authorization_id=authorized.authorization.authorization_id,
            adoption_decision_id=authorized.authorization.adoption_decision_id,
            adoption_policy_version=authorized.authorization.adoption_policy_version,
            swap_evidence_id=evaluation_input.swap_evidence.swap_evidence_id,
            evaluated_at=evaluation_input.evaluated_at,
            outcome=EntryEvaluationOutcome.SKIP,
            candidate=None,
            skip_reason=reason,
        )

    @property
    def identity_payload(self) -> dict[str, object]:
        result = (
            self.candidate.evaluation_result_payload
            if self.candidate is not None
            else {"skip_reason": self.skip_reason.value if self.skip_reason else None}
        )
        return {
            "evaluation_contract_version": self.evaluation_contract_version,
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "strategy_config_identity": self.strategy_config_identity,
            "pair": self.pair.symbol,
            "signal_id": self.signal_id.value,
            "authorization_id": self.authorization_id,
            "adoption_decision_id": self.adoption_decision_id,
            "adoption_policy_version": self.adoption_policy_version,
            "swap_evidence_id": self.swap_evidence_id,
            "evaluated_at": self.evaluated_at.isoformat(),
            "outcome": self.outcome.value,
            # Candidate ID is derived after this payload; including it would create
            # a circular evaluation-ID/candidate-ID dependency.
            "result": result,
        }

    @property
    def expected_evaluation_id(self) -> str:
        return "strategy-entry-evaluation-" + digest(self.identity_payload)

    def _validate_candidate_lineage(self, candidate: ProductionTradeCandidate) -> None:
        if (
            candidate.strategy_evaluation_id != self.evaluation_id
            or candidate.strategy_id != self.strategy_id
            or candidate.strategy_version != self.strategy_version
            or candidate.strategy_config_identity != self.strategy_config_identity
            or candidate.pair != self.pair
            or candidate.signal_id != self.signal_id
            or candidate.authorization_id != self.authorization_id
            or candidate.swap_evidence_id != self.swap_evidence_id
            or candidate.created_at != self.evaluated_at
        ):
            raise ValueError("candidate lineage does not match entry evaluation")


@dataclass(frozen=True, slots=True)
class PositionExitPositionEvidence:
    position_id: PositionId
    position_evidence_id: str
    pair: CurrencyPair
    existing_position_side: Side
    position_opened_at: datetime
    position_observed_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.position_id, PositionId):
            raise TypeError("position_id must be PositionId")
        _require_text(self.position_evidence_id, "position_evidence_id")
        if not isinstance(self.pair, CurrencyPair):
            raise TypeError("pair must be CurrencyPair")
        if not isinstance(self.existing_position_side, Side):
            raise TypeError("existing_position_side must be Side")
        require_utc(self.position_opened_at, "position opened_at")
        require_utc(self.position_observed_at, "position observed_at")
        if self.position_opened_at > self.position_observed_at:
            raise ValueError("position_opened_at cannot be after position_observed_at")

    @property
    def identity_payload(self) -> dict[str, object]:
        return {
            "position_id": self.position_id.value,
            "position_evidence_id": self.position_evidence_id,
            "pair": self.pair.symbol,
            "existing_position_side": self.existing_position_side.value,
            "position_opened_at": self.position_opened_at.isoformat(),
            "position_observed_at": self.position_observed_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class PositionExitEvidenceContext:
    position: PositionExitPositionEvidence
    signal_selection_checkpoint_id: str
    swap_selection_checkpoint_id: str
    expected_signal_specification_identity: str
    prior_adoption_decision_id: str
    adoption_state_evidence_id: str
    exit_input_policy_version: str

    def __post_init__(self) -> None:
        if not isinstance(self.position, PositionExitPositionEvidence):
            raise TypeError("position must be PositionExitPositionEvidence")
        for value, label in (
            (self.signal_selection_checkpoint_id, "signal_selection_checkpoint_id"),
            (self.swap_selection_checkpoint_id, "swap_selection_checkpoint_id"),
            (
                self.expected_signal_specification_identity,
                "expected_signal_specification_identity",
            ),
            (self.prior_adoption_decision_id, "prior_adoption_decision_id"),
            (self.adoption_state_evidence_id, "adoption_state_evidence_id"),
            (self.exit_input_policy_version, "exit_input_policy_version"),
        ):
            _require_text(value, label)

    @property
    def position_evidence_id(self) -> str:
        return self.position.position_evidence_id

    @property
    def position_opened_at(self) -> datetime:
        return self.position.position_opened_at

    @property
    def position_observed_at(self) -> datetime:
        return self.position.position_observed_at

    @property
    def identity_payload(self) -> dict[str, object]:
        return {
            "position": self.position.identity_payload,
            "signal_selection_checkpoint_id": self.signal_selection_checkpoint_id,
            "swap_selection_checkpoint_id": self.swap_selection_checkpoint_id,
            "expected_signal_specification_identity": (
                self.expected_signal_specification_identity
            ),
            "prior_adoption_decision_id": self.prior_adoption_decision_id,
            "adoption_state_evidence_id": self.adoption_state_evidence_id,
            "exit_input_policy_version": self.exit_input_policy_version,
        }


@dataclass(frozen=True, slots=True)
class PositionCloseEvidenceLineage:
    context: PositionExitEvidenceContext
    authorized_pair_signal: AuthorizedSignal | None
    swap_evidence: OperationalSwapEvidence | None

    def __post_init__(self) -> None:
        if not isinstance(self.context, PositionExitEvidenceContext):
            raise TypeError("context must be PositionExitEvidenceContext")
        if self.authorized_pair_signal is not None and not isinstance(
            self.authorized_pair_signal, AuthorizedSignal
        ):
            raise TypeError("authorized_pair_signal must be AuthorizedSignal when present")
        if self.swap_evidence is not None:
            self.swap_evidence.validate_intrinsic_integrity()

    def validate_for(
        self,
        *,
        strategy_id: str,
        strategy_version: str,
        position_id: PositionId,
        pair: CurrencyPair,
        existing_position_side: Side,
        evaluated_at: datetime,
    ) -> None:
        require_utc(evaluated_at, "position exit evaluated_at")
        position = self.context.position
        if position.position_id != position_id:
            raise ValueError("Position evidence belongs to another Position")
        if position.pair != pair:
            raise ValueError("Position evidence belongs to another Pair")
        if position.existing_position_side is not existing_position_side:
            raise ValueError("Position evidence has another existing side")
        if self.context.position_observed_at > evaluated_at:
            raise ValueError("position_observed_at cannot be after evaluated_at")
        if self.authorized_pair_signal is not None:
            signal = self.authorized_pair_signal.signal
            authorization = self.authorized_pair_signal.authorization
            if authorization.signal_id != signal.signal_id.value:
                raise ValueError("authorization does not belong to current Signal")
            if (
                authorization.strategy_id != strategy_id
                or authorization.strategy_version != strategy_version
            ):
                raise ValueError("Signal authorization belongs to another Strategy")
            if not isinstance(signal.target, PairTarget) or not isinstance(
                signal.direction, PairScore
            ):
                raise TypeError("position exit requires an authorized Pair Signal")
            if signal.target.pair != pair:
                raise ValueError("authorized Pair Signal belongs to another Pair")
            if signal.created_at > evaluated_at:
                raise ValueError("Signal created_at cannot be after evaluated_at")
            if authorization.authorized_at > evaluated_at:
                raise ValueError("Signal authorized_at cannot be after evaluated_at")
        if self.swap_evidence is not None:
            self.swap_evidence.validate_intrinsic_integrity()
            if self.swap_evidence.pair != pair:
                raise ValueError("swap evidence belongs to another Pair")
            if self.swap_evidence.received_at > evaluated_at:
                raise ValueError("swap received_at cannot be after evaluated_at")

    @property
    def identity_payload(self) -> dict[str, object]:
        return {
            "context": self.context.identity_payload,
            "authorized_signal_identity": (
                None
                if self.authorized_pair_signal is None
                else _authorized_signal_identity(self.authorized_pair_signal)
            ),
            "swap_evidence_identity": (
                None
                if self.swap_evidence is None
                else {
                    "swap_evidence_id": self.swap_evidence.swap_evidence_id,
                    "intrinsic_identity": self.swap_evidence.identity_payload,
                }
            ),
        }

    @property
    def position_evidence_id(self) -> str:
        return self.context.position_evidence_id

    @property
    def position(self) -> PositionExitPositionEvidence:
        return self.context.position

    @property
    def signal_id(self) -> SignalId | None:
        if self.authorized_pair_signal is None:
            return None
        return self.authorized_pair_signal.signal.signal_id

    @property
    def authorization_id(self) -> str | None:
        if self.authorized_pair_signal is None:
            return None
        return self.authorized_pair_signal.authorization.authorization_id

    @property
    def current_adoption_decision_id(self) -> str | None:
        if self.authorized_pair_signal is None:
            return None
        return self.authorized_pair_signal.authorization.adoption_decision_id

    @property
    def swap_evidence_id(self) -> str | None:
        if self.swap_evidence is None:
            return None
        return self.swap_evidence.swap_evidence_id


@dataclass(frozen=True, slots=True)
class ProductionPositionExitEvaluationInput:
    strategy_id: str
    strategy_version: str
    approved_strategy_config_identity: str
    position_id: PositionId
    pair: CurrencyPair
    existing_position_side: Side
    evidence_context: PositionExitEvidenceContext
    authorized_pair_signal: AuthorizedSignal | None
    swap_evidence: OperationalSwapEvidence | None
    evaluated_at: datetime

    def __post_init__(self) -> None:
        for value, label in (
            (self.strategy_id, "strategy_id"),
            (self.strategy_version, "strategy_version"),
            (self.approved_strategy_config_identity, "approved_strategy_config_identity"),
        ):
            _require_text(value, label)
        self.evidence_lineage.validate_for(
            strategy_id=self.strategy_id,
            strategy_version=self.strategy_version,
            position_id=self.position_id,
            pair=self.pair,
            existing_position_side=self.existing_position_side,
            evaluated_at=self.evaluated_at,
        )

    @property
    def evidence_lineage(self) -> PositionCloseEvidenceLineage:
        return PositionCloseEvidenceLineage(
            context=self.evidence_context,
            authorized_pair_signal=self.authorized_pair_signal,
            swap_evidence=self.swap_evidence,
        )


@dataclass(frozen=True, slots=True)
class PositionCloseCandidate:
    close_candidate_id: str
    close_candidate_contract_version: str
    strategy_id: str
    strategy_version: str
    strategy_config_identity: str
    strategy_evaluation_id: str
    position_id: PositionId
    pair: CurrencyPair
    existing_position_side: Side
    exit_reason: PositionExitReason
    evidence_lineage: PositionCloseEvidenceLineage
    created_at: datetime

    def __post_init__(self) -> None:
        if self.close_candidate_contract_version != POSITION_CLOSE_CANDIDATE_CONTRACT_VERSION:
            raise ValueError("unsupported PositionCloseCandidate contract")
        for value, label in (
            (self.close_candidate_id, "close_candidate_id"),
            (self.strategy_id, "strategy_id"),
            (self.strategy_version, "strategy_version"),
            (self.strategy_config_identity, "strategy_config_identity"),
            (self.strategy_evaluation_id, "strategy_evaluation_id"),
        ):
            _require_text(value, label)
        if not isinstance(self.exit_reason, PositionExitReason):
            raise TypeError("exit_reason must be PositionExitReason")
        self.evidence_lineage.validate_for(
            strategy_id=self.strategy_id,
            strategy_version=self.strategy_version,
            position_id=self.position_id,
            pair=self.pair,
            existing_position_side=self.existing_position_side,
            evaluated_at=self.created_at,
        )
        _require_exit_reason_evidence(
            self.evidence_lineage,
            self.exit_reason,
            strategy_id=self.strategy_id,
            strategy_version=self.strategy_version,
            strategy_config_identity=self.strategy_config_identity,
            evaluated_at=self.created_at,
        )
        if self.close_candidate_id != self.expected_close_candidate_id:
            raise ValueError("close_candidate_id does not match intrinsic candidate")

    @classmethod
    def create(
        cls,
        *,
        close_candidate_contract_version: str,
        strategy_id: str,
        strategy_version: str,
        strategy_config_identity: str,
        strategy_evaluation_id: str,
        position_id: PositionId,
        pair: CurrencyPair,
        existing_position_side: Side,
        exit_reason: PositionExitReason,
        evidence_lineage: PositionCloseEvidenceLineage,
        created_at: datetime,
    ) -> "PositionCloseCandidate":
        payload = _close_candidate_payload(
            close_candidate_contract_version=close_candidate_contract_version,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            strategy_config_identity=strategy_config_identity,
            strategy_evaluation_id=strategy_evaluation_id,
            position_id=position_id,
            pair=pair,
            existing_position_side=existing_position_side,
            exit_reason=exit_reason,
            evidence_lineage=evidence_lineage,
            created_at=created_at,
        )
        return cls(
            close_candidate_id="position-close-candidate-" + digest(payload),
            close_candidate_contract_version=close_candidate_contract_version,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            strategy_config_identity=strategy_config_identity,
            strategy_evaluation_id=strategy_evaluation_id,
            position_id=position_id,
            pair=pair,
            existing_position_side=existing_position_side,
            exit_reason=exit_reason,
            evidence_lineage=evidence_lineage,
            created_at=created_at,
        )

    @property
    def reduce_only(self) -> bool:
        return True

    @property
    def close_side(self) -> Side:
        return Side.SELL if self.existing_position_side is Side.BUY else Side.BUY

    @property
    def identity_payload(self) -> dict[str, object]:
        return _close_candidate_payload(
            close_candidate_contract_version=self.close_candidate_contract_version,
            strategy_id=self.strategy_id,
            strategy_version=self.strategy_version,
            strategy_config_identity=self.strategy_config_identity,
            strategy_evaluation_id=self.strategy_evaluation_id,
            position_id=self.position_id,
            pair=self.pair,
            existing_position_side=self.existing_position_side,
            exit_reason=self.exit_reason,
            evidence_lineage=self.evidence_lineage,
            created_at=self.created_at,
        )

    @property
    def expected_close_candidate_id(self) -> str:
        return "position-close-candidate-" + digest(self.identity_payload)


@dataclass(frozen=True, slots=True)
class ProductionPositionExitEvaluation:
    evaluation_id: str
    evaluation_contract_version: str
    strategy_id: str
    strategy_version: str
    strategy_config_identity: str
    position_id: PositionId
    pair: CurrencyPair
    existing_position_side: Side
    evidence_lineage: PositionCloseEvidenceLineage
    evaluated_at: datetime
    outcome: PositionExitEvaluationOutcome
    close_candidate: PositionCloseCandidate | None
    keep_reason: PositionExitKeepReason | None

    def __post_init__(self) -> None:
        if self.evaluation_contract_version != POSITION_EXIT_EVALUATION_CONTRACT_VERSION:
            raise ValueError("unsupported ProductionPositionExitEvaluation contract")
        for value, label in (
            (self.evaluation_id, "evaluation_id"),
            (self.strategy_id, "strategy_id"),
            (self.strategy_version, "strategy_version"),
            (self.strategy_config_identity, "strategy_config_identity"),
        ):
            _require_text(value, label)
        self.evidence_lineage.validate_for(
            strategy_id=self.strategy_id,
            strategy_version=self.strategy_version,
            position_id=self.position_id,
            pair=self.pair,
            existing_position_side=self.existing_position_side,
            evaluated_at=self.evaluated_at,
        )
        if self.outcome is PositionExitEvaluationOutcome.CLOSE_CANDIDATE:
            if self.close_candidate is None or self.keep_reason is not None:
                raise ValueError("CLOSE_CANDIDATE requires only close_candidate")
            if (
                self.close_candidate.strategy_evaluation_id != self.evaluation_id
                or self.close_candidate.strategy_id != self.strategy_id
                or self.close_candidate.strategy_version != self.strategy_version
                or self.close_candidate.strategy_config_identity
                != self.strategy_config_identity
                or self.close_candidate.position_id != self.position_id
                or self.close_candidate.pair != self.pair
                or self.close_candidate.existing_position_side
                is not self.existing_position_side
                or self.close_candidate.evidence_lineage != self.evidence_lineage
                or self.close_candidate.created_at != self.evaluated_at
            ):
                raise ValueError("close candidate lineage does not match exit evaluation")
        elif self.close_candidate is not None or self.keep_reason is None:
            raise ValueError("KEEP requires only keep_reason")
        if self.evaluation_id != self.expected_evaluation_id:
            raise ValueError("position exit evaluation ID does not match content")

    @classmethod
    def create_close_candidate(
        cls,
        evaluation_input: ProductionPositionExitEvaluationInput,
        *,
        close_candidate_contract_version: str,
        exit_reason: PositionExitReason,
    ) -> "ProductionPositionExitEvaluation":
        lineage = evaluation_input.evidence_lineage
        _require_exit_reason_evidence(
            lineage,
            exit_reason,
            strategy_id=evaluation_input.strategy_id,
            strategy_version=evaluation_input.strategy_version,
            strategy_config_identity=(
                evaluation_input.approved_strategy_config_identity
            ),
            evaluated_at=evaluation_input.evaluated_at,
        )
        result: dict[str, object] = {
            "close_candidate_contract_version": close_candidate_contract_version,
            "exit_reason": exit_reason.value,
            "created_at": evaluation_input.evaluated_at.isoformat(),
        }
        common = _position_exit_common_payload(evaluation_input)
        evaluation_id = _position_exit_evaluation_id(
            common,
            outcome=PositionExitEvaluationOutcome.CLOSE_CANDIDATE,
            result=result,
        )
        candidate = PositionCloseCandidate.create(
            close_candidate_contract_version=close_candidate_contract_version,
            strategy_id=evaluation_input.strategy_id,
            strategy_version=evaluation_input.strategy_version,
            strategy_config_identity=evaluation_input.approved_strategy_config_identity,
            strategy_evaluation_id=evaluation_id,
            position_id=evaluation_input.position_id,
            pair=evaluation_input.pair,
            existing_position_side=evaluation_input.existing_position_side,
            exit_reason=exit_reason,
            evidence_lineage=lineage,
            created_at=evaluation_input.evaluated_at,
        )
        return cls(
            evaluation_id=evaluation_id,
            evaluation_contract_version=POSITION_EXIT_EVALUATION_CONTRACT_VERSION,
            strategy_id=evaluation_input.strategy_id,
            strategy_version=evaluation_input.strategy_version,
            strategy_config_identity=evaluation_input.approved_strategy_config_identity,
            position_id=evaluation_input.position_id,
            pair=evaluation_input.pair,
            existing_position_side=evaluation_input.existing_position_side,
            evidence_lineage=lineage,
            evaluated_at=evaluation_input.evaluated_at,
            outcome=PositionExitEvaluationOutcome.CLOSE_CANDIDATE,
            close_candidate=candidate,
            keep_reason=None,
        )

    @classmethod
    def create_keep(
        cls,
        evaluation_input: ProductionPositionExitEvaluationInput,
        *,
        reason: PositionExitKeepReason,
    ) -> "ProductionPositionExitEvaluation":
        lineage = evaluation_input.evidence_lineage
        common = _position_exit_common_payload(evaluation_input)
        evaluation_id = _position_exit_evaluation_id(
            common,
            outcome=PositionExitEvaluationOutcome.KEEP,
            result={"keep_reason": reason.value},
        )
        return cls(
            evaluation_id=evaluation_id,
            evaluation_contract_version=POSITION_EXIT_EVALUATION_CONTRACT_VERSION,
            strategy_id=evaluation_input.strategy_id,
            strategy_version=evaluation_input.strategy_version,
            strategy_config_identity=evaluation_input.approved_strategy_config_identity,
            position_id=evaluation_input.position_id,
            pair=evaluation_input.pair,
            existing_position_side=evaluation_input.existing_position_side,
            evidence_lineage=lineage,
            evaluated_at=evaluation_input.evaluated_at,
            outcome=PositionExitEvaluationOutcome.KEEP,
            close_candidate=None,
            keep_reason=reason,
        )

    @property
    def identity_payload(self) -> dict[str, object]:
        if self.close_candidate is None:
            result: dict[str, object] = {
                "keep_reason": self.keep_reason.value if self.keep_reason else None
            }
        else:
            result = {
                "close_candidate_contract_version": (
                    self.close_candidate.close_candidate_contract_version
                ),
                "exit_reason": self.close_candidate.exit_reason.value,
                "created_at": self.close_candidate.created_at.isoformat(),
            }
        return {
            **_position_exit_common_fields_payload(
                evaluation_contract_version=self.evaluation_contract_version,
                strategy_id=self.strategy_id,
                strategy_version=self.strategy_version,
                strategy_config_identity=self.strategy_config_identity,
                position_id=self.position_id,
                pair=self.pair,
                existing_position_side=self.existing_position_side,
                evidence_lineage=self.evidence_lineage,
                evaluated_at=self.evaluated_at,
            ),
            "outcome": self.outcome.value,
            "result": result,
        }

    @property
    def expected_evaluation_id(self) -> str:
        return "strategy-position-exit-evaluation-" + digest(self.identity_payload)


class ProductionEntryStrategy(Protocol):
    def evaluate_entry(
        self, evaluation_input: ProductionEntryEvaluationInput
    ) -> ProductionEntryEvaluation: ...


class ProductionPositionExitStrategy(Protocol):
    def evaluate_position(
        self, evaluation_input: ProductionPositionExitEvaluationInput
    ) -> ProductionPositionExitEvaluation: ...


def _entry_common_payload(
    evaluation_input: ProductionEntryEvaluationInput, pair: CurrencyPair
) -> dict[str, object]:
    authorized = evaluation_input.authorized_pair_signal
    return {
        "evaluation_contract_version": ENTRY_EVALUATION_CONTRACT_VERSION,
        "strategy_id": authorized.authorization.strategy_id,
        "strategy_version": authorized.authorization.strategy_version,
        "strategy_config_identity": evaluation_input.approved_strategy_config_identity,
        "pair": pair.symbol,
        "signal_id": authorized.signal.signal_id.value,
        "authorization_id": authorized.authorization.authorization_id,
        "adoption_decision_id": authorized.authorization.adoption_decision_id,
        "adoption_policy_version": authorized.authorization.adoption_policy_version,
        "swap_evidence_id": evaluation_input.swap_evidence.swap_evidence_id,
        "evaluated_at": evaluation_input.evaluated_at.isoformat(),
    }


def _entry_evaluation_id(
    common: dict[str, object],
    *,
    outcome: EntryEvaluationOutcome,
    result: dict[str, object],
) -> str:
    return "strategy-entry-evaluation-" + digest(
        {**common, "outcome": outcome.value, "result": result}
    )


def _close_candidate_payload(
    *,
    close_candidate_contract_version: str,
    strategy_id: str,
    strategy_version: str,
    strategy_config_identity: str,
    strategy_evaluation_id: str,
    position_id: PositionId,
    pair: CurrencyPair,
    existing_position_side: Side,
    exit_reason: PositionExitReason,
    evidence_lineage: PositionCloseEvidenceLineage,
    created_at: datetime,
) -> dict[str, object]:
    return {
        "close_candidate_contract_version": close_candidate_contract_version,
        "strategy_id": strategy_id,
        "strategy_version": strategy_version,
        "strategy_config_identity": strategy_config_identity,
        "strategy_evaluation_id": strategy_evaluation_id,
        "position_id": position_id.value,
        "pair": pair.symbol,
        "existing_position_side": existing_position_side.value,
        "exit_reason": exit_reason.value,
        "evidence_lineage": evidence_lineage.identity_payload,
        "created_at": created_at.isoformat(),
    }


def _position_exit_common_payload(
    evaluation_input: ProductionPositionExitEvaluationInput,
) -> dict[str, object]:
    return _position_exit_common_fields_payload(
        evaluation_contract_version=POSITION_EXIT_EVALUATION_CONTRACT_VERSION,
        strategy_id=evaluation_input.strategy_id,
        strategy_version=evaluation_input.strategy_version,
        strategy_config_identity=evaluation_input.approved_strategy_config_identity,
        position_id=evaluation_input.position_id,
        pair=evaluation_input.pair,
        existing_position_side=evaluation_input.existing_position_side,
        evidence_lineage=evaluation_input.evidence_lineage,
        evaluated_at=evaluation_input.evaluated_at,
    )


def _position_exit_common_fields_payload(
    *,
    evaluation_contract_version: str,
    strategy_id: str,
    strategy_version: str,
    strategy_config_identity: str,
    position_id: PositionId,
    pair: CurrencyPair,
    existing_position_side: Side,
    evidence_lineage: PositionCloseEvidenceLineage,
    evaluated_at: datetime,
) -> dict[str, object]:
    return {
        "evaluation_contract_version": evaluation_contract_version,
        "strategy_id": strategy_id,
        "strategy_version": strategy_version,
        "strategy_config_identity": strategy_config_identity,
        "position_id": position_id.value,
        "pair": pair.symbol,
        "existing_position_side": existing_position_side.value,
        "evidence_lineage": evidence_lineage.identity_payload,
        "evaluated_at": evaluated_at.isoformat(),
    }


def _position_exit_evaluation_id(
    common: dict[str, object],
    *,
    outcome: PositionExitEvaluationOutcome,
    result: dict[str, object],
) -> str:
    return "strategy-position-exit-evaluation-" + digest(
        {**common, "outcome": outcome.value, "result": result}
    )


def _authorized_signal_identity(authorized: AuthorizedSignal) -> dict[str, object]:
    signal = authorized.signal
    authorization = authorized.authorization
    if not isinstance(signal.target, PairTarget) or not isinstance(
        signal.direction, PairScore
    ):
        raise TypeError("position exit requires an authorized Pair Signal")
    return {
        "signal_id": signal.signal_id.value,
        "authorization_id": authorization.authorization_id,
        "adoption_decision_id": authorization.adoption_decision_id,
        "evidence_snapshot_id": authorization.evidence_snapshot_id,
        "adoption_policy_version": authorization.adoption_policy_version,
        "strategy_id": authorization.strategy_id,
        "strategy_version": authorization.strategy_version,
        "adoption_mode": authorization.adoption_mode.value,
        "runtime_mode": authorization.runtime_mode.value,
        "authorized_at": authorization.authorized_at.isoformat(),
        "signal": {
            "target_pair": signal.target.pair.symbol,
            "signal_type": signal.signal_type,
            "direction": signal.direction.value,
            "strength": signal.strength.value,
            "confidence": signal.confidence.value,
            "horizon": signal.horizon.value,
            "observed_at": signal.observed_at.isoformat(),
            "created_at": signal.created_at.isoformat(),
            "source_feature_ids": [
                feature_id.value for feature_id in signal.source_feature_ids
            ],
            "versions": {
                "producer_version": signal.versions.producer_version,
                "model_version": signal.versions.model_version,
                "prompt_version": signal.versions.prompt_version,
                "scorer_version": signal.versions.scorer_version,
                "transformation_version": signal.versions.transformation_version,
            },
        },
    }


def _require_exit_reason_evidence(
    lineage: PositionCloseEvidenceLineage,
    reason: PositionExitReason,
    *,
    strategy_id: str,
    strategy_version: str,
    strategy_config_identity: str,
    evaluated_at: datetime,
) -> None:
    if not isinstance(reason, PositionExitReason):
        raise ValueError("unsupported Position exit reason")
    position = lineage.position
    context = lineage.context
    match reason:
        case PositionExitReason.SIGNAL_REVERSED:
            authorized = lineage.authorized_pair_signal
            if authorized is None:
                raise ValueError(
                    "SIGNAL_REVERSED requires current AuthorizedSignal evidence"
                )
            signal = authorized.signal
            authorization = authorized.authorization
            if not isinstance(signal.target, PairTarget) or not isinstance(
                signal.direction, PairScore
            ):
                raise ValueError("SIGNAL_REVERSED requires an authorized Pair Signal")
            if signal.target.pair != position.pair:
                raise ValueError("SIGNAL_REVERSED Signal belongs to another Pair")
            if (
                authorization.strategy_id != strategy_id
                or authorization.strategy_version != strategy_version
            ):
                raise ValueError("SIGNAL_REVERSED authorization belongs to another Strategy")
        case PositionExitReason.CARRY_NO_LONGER_POSITIVE:
            swap = lineage.swap_evidence
            if swap is None:
                raise ValueError(
                    "CARRY_NO_LONGER_POSITIVE requires current OperationalSwapEvidence"
                )
            if swap.pair != position.pair:
                raise ValueError(
                    "CARRY_NO_LONGER_POSITIVE Swap belongs to another Pair"
                )
        case PositionExitReason.MAXIMUM_HOLDING_AGE:
            require_utc(evaluated_at, "position exit evaluated_at")
            _require_text(strategy_config_identity, "strategy_config_identity")
            _require_text(context.exit_input_policy_version, "exit_input_policy_version")
            if position.position_opened_at > evaluated_at:
                raise ValueError("position_opened_at cannot be after evaluated_at")
        case PositionExitReason.ADOPTION_NO_LONGER_ACTIVE:
            _require_text(
                context.prior_adoption_decision_id,
                "prior_adoption_decision_id",
            )
            _require_text(
                context.adoption_state_evidence_id,
                "adoption_state_evidence_id",
            )
        case PositionExitReason.REQUIRED_SIGNAL_MISSING_OR_STALE:
            _require_text(
                context.signal_selection_checkpoint_id,
                "signal_selection_checkpoint_id",
            )
            _require_text(
                context.expected_signal_specification_identity,
                "expected_signal_specification_identity",
            )
            _require_text(context.exit_input_policy_version, "exit_input_policy_version")
        case PositionExitReason.REQUIRED_SWAP_MISSING_OR_STALE:
            _require_text(
                context.swap_selection_checkpoint_id,
                "swap_selection_checkpoint_id",
            )
            _require_text(context.exit_input_policy_version, "exit_input_policy_version")
        case _:
            raise ValueError("unsupported Position exit reason")


def _require_text(value: str, label: str) -> None:
    if not value.strip():
        raise ValueError(f"{label} must not be blank")
