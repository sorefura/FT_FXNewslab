from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from fx_core import (
    Currency,
    CurrencyPair,
    CurrencyTarget,
    DirectionScore,
    FeatureId,
    Horizon,
    ObservationId,
    PairScore,
    PairTarget,
    Probability,
    Signal,
    SignalId,
    VersionMetadata,
)
from fx_core.identity import digest
from fx_core.time import require_utc

from .store import SignalLineage

PAIR_SIGNAL_MATERIALIZATION_SPEC_VERSION = "pair-signal-materialization-spec-v1"
PAIR_SIGNAL_MATERIALIZATION_REQUEST_VERSION = "pair-signal-materialization-request-v1"
SIGNAL_CONTENT_SNAPSHOT_VERSION = "signal-content-snapshot-v1"
PAIR_SIGNAL_SELECTION_CANDIDATE_VERSION = "pair-signal-selection-candidate-v1"
PAIR_SIGNAL_SELECTION_SNAPSHOT_VERSION = "pair-signal-selection-snapshot-v1"
PAIR_SIGNAL_DERIVATION_VERSION = "pair-signal-derivation-v1"
PAIR_SIGNAL_IDENTITY_VERSION = "pair-signal-identity-v1"

SUPPORTED_SOURCE_SIGNAL_TYPE = "currency_fundamental"
SUPPORTED_OUTPUT_SIGNAL_TYPE = "pair_fundamental"
SUPPORTED_PAIR_TRANSFORMATION_VERSION = "currency-pair-v1"
SUPPORTED_OBSERVATION_GROUP_POLICY_VERSION = "exact-observation-set-v1"
SUPPORTED_SELECTION_POLICY_VERSION = "pair-signal-selection-v1"


class SignalTargetType(StrEnum):
    CURRENCY = "currency"
    PAIR = "pair"


class SignalDirectionType(StrEnum):
    DIRECTION_SCORE = "direction_score"
    PAIR_SCORE = "pair_score"


class SourceSignalRole(StrEnum):
    BASE = "BASE"
    QUOTE = "QUOTE"


class PairSignalCandidateEligibility(StrEnum):
    ELIGIBLE = "ELIGIBLE"
    INELIGIBLE = "INELIGIBLE"


class PairSignalCandidateRejectionReason(StrEnum):
    TARGET_TYPE_MISMATCH = "TARGET_TYPE_MISMATCH"
    TARGET_CURRENCY_MISMATCH = "TARGET_CURRENCY_MISMATCH"
    SIGNAL_TYPE_MISMATCH = "SIGNAL_TYPE_MISMATCH"
    HORIZON_MISMATCH = "HORIZON_MISMATCH"
    PRODUCER_VERSION_MISMATCH = "PRODUCER_VERSION_MISMATCH"
    MODEL_VERSION_MISMATCH = "MODEL_VERSION_MISMATCH"
    PROMPT_VERSION_MISMATCH = "PROMPT_VERSION_MISMATCH"
    SCORER_VERSION_MISMATCH = "SCORER_VERSION_MISMATCH"
    SOURCE_TRANSFORMATION_VERSION_MISMATCH = "SOURCE_TRANSFORMATION_VERSION_MISMATCH"
    DIRECTION_TYPE_MISMATCH = "DIRECTION_TYPE_MISMATCH"
    CREATED_AFTER_AS_OF = "CREATED_AFTER_AS_OF"
    OBSERVED_AFTER_AS_OF = "OBSERVED_AFTER_AS_OF"
    STALE_AT_AS_OF = "STALE_AT_AS_OF"


class PairSignalSelectionOutcome(StrEnum):
    SELECTED = "SELECTED"
    NO_MATCH = "NO_MATCH"
    AMBIGUOUS = "AMBIGUOUS"


class PairSignalSelectionReason(StrEnum):
    SELECTED_EXACT_GROUP = "SELECTED_EXACT_GROUP"
    NO_ELIGIBLE_BASE_SIGNAL = "NO_ELIGIBLE_BASE_SIGNAL"
    NO_ELIGIBLE_QUOTE_SIGNAL = "NO_ELIGIBLE_QUOTE_SIGNAL"
    NO_COMPLETE_OBSERVATION_GROUP = "NO_COMPLETE_OBSERVATION_GROUP"
    AMBIGUOUS_BASE_SIGNAL = "AMBIGUOUS_BASE_SIGNAL"
    AMBIGUOUS_QUOTE_SIGNAL = "AMBIGUOUS_QUOTE_SIGNAL"
    AMBIGUOUS_SOURCE_GROUP = "AMBIGUOUS_SOURCE_GROUP"


@dataclass(frozen=True, slots=True)
class PairSignalMaterializationSpecification:
    specification_id: str
    contract_version: str
    pair: CurrencyPair
    source_signal_type: str
    output_signal_type: str
    horizon: Horizon
    producer_version: str
    model_version: str
    prompt_version: str
    scorer_version: str
    expected_source_transformation_version: str | None
    output_transformation_version: str
    source_signal_max_age: timedelta
    observation_group_policy_version: str
    selection_policy_version: str

    def __post_init__(self) -> None:
        self.validate_intrinsic_integrity()

    @classmethod
    def create(
        cls,
        *,
        contract_version: str,
        pair: CurrencyPair,
        source_signal_type: str,
        output_signal_type: str,
        horizon: Horizon,
        producer_version: str,
        model_version: str,
        prompt_version: str,
        scorer_version: str,
        expected_source_transformation_version: str | None,
        output_transformation_version: str,
        source_signal_max_age: timedelta,
        observation_group_policy_version: str,
        selection_policy_version: str,
    ) -> "PairSignalMaterializationSpecification":
        payload = _specification_payload(
            contract_version=contract_version,
            pair=pair,
            source_signal_type=source_signal_type,
            output_signal_type=output_signal_type,
            horizon=horizon,
            producer_version=producer_version,
            model_version=model_version,
            prompt_version=prompt_version,
            scorer_version=scorer_version,
            expected_source_transformation_version=expected_source_transformation_version,
            output_transformation_version=output_transformation_version,
            source_signal_max_age=source_signal_max_age,
            observation_group_policy_version=observation_group_policy_version,
            selection_policy_version=selection_policy_version,
        )
        return cls(
            specification_id="pair-signal-spec-" + digest(payload),
            contract_version=contract_version,
            pair=pair,
            source_signal_type=source_signal_type,
            output_signal_type=output_signal_type,
            horizon=horizon,
            producer_version=producer_version,
            model_version=model_version,
            prompt_version=prompt_version,
            scorer_version=scorer_version,
            expected_source_transformation_version=expected_source_transformation_version,
            output_transformation_version=output_transformation_version,
            source_signal_max_age=source_signal_max_age,
            observation_group_policy_version=observation_group_policy_version,
            selection_policy_version=selection_policy_version,
        )

    @property
    def identity_payload(self) -> dict[str, object]:
        return _specification_payload(
            contract_version=self.contract_version,
            pair=self.pair,
            source_signal_type=self.source_signal_type,
            output_signal_type=self.output_signal_type,
            horizon=self.horizon,
            producer_version=self.producer_version,
            model_version=self.model_version,
            prompt_version=self.prompt_version,
            scorer_version=self.scorer_version,
            expected_source_transformation_version=self.expected_source_transformation_version,
            output_transformation_version=self.output_transformation_version,
            source_signal_max_age=self.source_signal_max_age,
            observation_group_policy_version=self.observation_group_policy_version,
            selection_policy_version=self.selection_policy_version,
        )

    @property
    def expected_specification_id(self) -> str:
        return "pair-signal-spec-" + digest(self.identity_payload)

    def validate_intrinsic_integrity(self) -> None:
        _require_text(self.specification_id, "specification_id")
        if self.contract_version != PAIR_SIGNAL_MATERIALIZATION_SPEC_VERSION:
            raise ValueError("unsupported Pair Signal materialization specification")
        if not isinstance(self.pair, CurrencyPair):
            raise TypeError("pair must be CurrencyPair")
        if not isinstance(self.horizon, Horizon):
            raise TypeError("horizon must be Horizon")
        for value, label in (
            (self.producer_version, "producer_version"),
            (self.model_version, "model_version"),
            (self.prompt_version, "prompt_version"),
            (self.scorer_version, "scorer_version"),
        ):
            _require_text(value, label)
        if self.source_signal_type != SUPPORTED_SOURCE_SIGNAL_TYPE:
            raise ValueError("unsupported source Signal type")
        if self.output_signal_type != SUPPORTED_OUTPUT_SIGNAL_TYPE:
            raise ValueError("unsupported output Signal type")
        if self.expected_source_transformation_version is not None:
            raise ValueError("v1 source Signals must not have a transformation version")
        if self.output_transformation_version != SUPPORTED_PAIR_TRANSFORMATION_VERSION:
            raise ValueError("unsupported output transformation version")
        if self.source_signal_max_age <= timedelta(0):
            raise ValueError("source_signal_max_age must be positive")
        if (
            self.observation_group_policy_version
            != SUPPORTED_OBSERVATION_GROUP_POLICY_VERSION
        ):
            raise ValueError("unsupported Observation group policy")
        if self.selection_policy_version != SUPPORTED_SELECTION_POLICY_VERSION:
            raise ValueError("unsupported Pair Signal selection policy")
        if self.specification_id != self.expected_specification_id:
            raise ValueError("specification_id does not match intrinsic content")


@dataclass(frozen=True, slots=True)
class PairSignalMaterializationRequest:
    request_id: str
    contract_version: str
    pair: CurrencyPair
    as_of: datetime
    specification: PairSignalMaterializationSpecification

    def __post_init__(self) -> None:
        self.validate_intrinsic_integrity()

    @classmethod
    def create(
        cls,
        *,
        contract_version: str,
        pair: CurrencyPair,
        as_of: datetime,
        specification: PairSignalMaterializationSpecification,
    ) -> "PairSignalMaterializationRequest":
        payload = _request_payload(
            contract_version=contract_version,
            pair=pair,
            as_of=as_of,
            specification=specification,
        )
        return cls(
            request_id="pair-signal-request-" + digest(payload),
            contract_version=contract_version,
            pair=pair,
            as_of=as_of,
            specification=specification,
        )

    @property
    def identity_payload(self) -> dict[str, object]:
        return _request_payload(
            contract_version=self.contract_version,
            pair=self.pair,
            as_of=self.as_of,
            specification=self.specification,
        )

    @property
    def expected_request_id(self) -> str:
        return "pair-signal-request-" + digest(self.identity_payload)

    def validate_intrinsic_integrity(self) -> None:
        _require_text(self.request_id, "request_id")
        if self.contract_version != PAIR_SIGNAL_MATERIALIZATION_REQUEST_VERSION:
            raise ValueError("unsupported Pair Signal materialization request")
        if not isinstance(self.pair, CurrencyPair):
            raise TypeError("request pair must be CurrencyPair")
        require_utc(self.as_of, "materialization request as_of")
        self.specification.validate_intrinsic_integrity()
        if self.pair != self.specification.pair:
            raise ValueError("request pair does not match specification pair")
        if self.request_id != self.expected_request_id:
            raise ValueError("request_id does not match intrinsic content")


@dataclass(frozen=True, slots=True)
class SignalContentSnapshot:
    signal_content_hash: str
    contract_version: str
    signal_id: SignalId
    target_type: SignalTargetType
    target_value: str
    signal_type: str
    direction_type: SignalDirectionType
    direction_value: float
    strength: float
    confidence: float
    horizon: Horizon
    observed_at: datetime
    created_at: datetime
    producer_version: str | None
    model_version: str | None
    prompt_version: str | None
    scorer_version: str
    transformation_version: str | None
    source_feature_ids: tuple[FeatureId, ...]
    source_observation_ids: tuple[ObservationId, ...]

    def __post_init__(self) -> None:
        self.validate_intrinsic_integrity()

    @classmethod
    def from_signal(
        cls,
        signal: Signal,
        lineage: SignalLineage,
    ) -> "SignalContentSnapshot":
        if lineage.signal_id != signal.signal_id:
            raise ValueError("Signal lineage belongs to another Signal")
        signal_features = _canonical_feature_ids(signal.source_feature_ids)
        lineage_features = _canonical_feature_ids(lineage.feature_ids)
        if signal_features != lineage_features:
            raise ValueError("Signal Feature lineage does not match Signal content")
        observations = canonical_observation_ids(lineage.observation_ids)
        if isinstance(signal.target, CurrencyTarget):
            target_type = SignalTargetType.CURRENCY
            target_value = signal.target.currency.code
            direction_type = SignalDirectionType.DIRECTION_SCORE
        elif isinstance(signal.target, PairTarget):
            target_type = SignalTargetType.PAIR
            target_value = signal.target.pair.symbol
            direction_type = SignalDirectionType.PAIR_SCORE
        else:
            raise TypeError("Signal target type is not supported")
        values: dict[str, object] = {
            "contract_version": SIGNAL_CONTENT_SNAPSHOT_VERSION,
            "signal_id": signal.signal_id,
            "target_type": target_type,
            "target_value": target_value,
            "signal_type": signal.signal_type,
            "direction_type": direction_type,
            "direction_value": signal.direction.value,
            "strength": signal.strength.value,
            "confidence": signal.confidence.value,
            "horizon": signal.horizon,
            "observed_at": signal.observed_at,
            "created_at": signal.created_at,
            "producer_version": signal.versions.producer_version,
            "model_version": signal.versions.model_version,
            "prompt_version": signal.versions.prompt_version,
            "scorer_version": signal.versions.scorer_version,
            "transformation_version": signal.versions.transformation_version,
            "source_feature_ids": signal_features,
            "source_observation_ids": observations,
        }
        payload = _signal_snapshot_payload(**values)  # type: ignore[arg-type]
        return cls(signal_content_hash="signal-content-" + digest(payload), **values)  # type: ignore[arg-type]

    @property
    def identity_payload(self) -> dict[str, object]:
        return _signal_snapshot_payload(
            contract_version=self.contract_version,
            signal_id=self.signal_id,
            target_type=self.target_type,
            target_value=self.target_value,
            signal_type=self.signal_type,
            direction_type=self.direction_type,
            direction_value=self.direction_value,
            strength=self.strength,
            confidence=self.confidence,
            horizon=self.horizon,
            observed_at=self.observed_at,
            created_at=self.created_at,
            producer_version=self.producer_version,
            model_version=self.model_version,
            prompt_version=self.prompt_version,
            scorer_version=self.scorer_version,
            transformation_version=self.transformation_version,
            source_feature_ids=self.source_feature_ids,
            source_observation_ids=self.source_observation_ids,
        )

    @property
    def expected_signal_content_hash(self) -> str:
        return "signal-content-" + digest(self.identity_payload)

    def validate_intrinsic_integrity(self) -> None:
        _require_text(self.signal_content_hash, "signal_content_hash")
        if self.contract_version != SIGNAL_CONTENT_SNAPSHOT_VERSION:
            raise ValueError("unsupported Signal content snapshot")
        if not isinstance(self.signal_id, SignalId):
            raise TypeError("signal_id must be SignalId")
        _require_text(self.target_value, "target_value")
        _require_text(self.signal_type, "signal_type")
        if self.target_type is SignalTargetType.CURRENCY:
            Currency(self.target_value)
            if self.direction_type is not SignalDirectionType.DIRECTION_SCORE:
                raise TypeError("Currency Signal snapshot requires DirectionScore")
            DirectionScore(self.direction_value)
        elif self.target_type is SignalTargetType.PAIR:
            CurrencyPair.parse(self.target_value)
            if self.direction_type is not SignalDirectionType.PAIR_SCORE:
                raise TypeError("Pair Signal snapshot requires PairScore")
            PairScore(self.direction_value)
        else:
            raise ValueError("unsupported Signal target type")
        Probability(self.strength)
        Probability(self.confidence)
        if not isinstance(self.horizon, Horizon):
            raise TypeError("snapshot horizon must be Horizon")
        require_utc(self.observed_at, "Signal snapshot observed_at")
        require_utc(self.created_at, "Signal snapshot created_at")
        if self.observed_at > self.created_at:
            raise ValueError("Signal snapshot cannot be created before observation")
        versions = VersionMetadata(
            producer_version=self.producer_version,
            model_version=self.model_version,
            prompt_version=self.prompt_version,
            scorer_version=self.scorer_version,
            transformation_version=self.transformation_version,
        )
        versions.require_signal_versions()
        if self.source_feature_ids != _canonical_feature_ids(self.source_feature_ids):
            raise ValueError("source_feature_ids must use canonical ordering")
        if self.source_observation_ids != canonical_observation_ids(
            self.source_observation_ids
        ):
            raise ValueError("source_observation_ids must use canonical ordering")
        if self.signal_content_hash != self.expected_signal_content_hash:
            raise ValueError("signal_content_hash does not match intrinsic content")


def canonical_observation_ids(
    observation_ids: Iterable[ObservationId],
) -> tuple[ObservationId, ...]:
    items = tuple(observation_ids)
    if not items:
        raise ValueError("Observation group must not be empty")
    if any(not isinstance(item, ObservationId) for item in items):
        raise TypeError("Observation group accepts only ObservationId")
    if len({item.value for item in items}) != len(items):
        raise ValueError("Observation group IDs must be unique")
    return tuple(sorted(items, key=lambda item: item.value))


def observation_group_identity(observation_ids: Iterable[ObservationId]) -> str:
    canonical = canonical_observation_ids(observation_ids)
    return "observation-group-" + digest([item.value for item in canonical])


@dataclass(frozen=True, slots=True)
class PairSignalSelectionCandidate:
    candidate_id: str
    contract_version: str
    request: PairSignalMaterializationRequest
    role: SourceSignalRole
    signal_snapshot: SignalContentSnapshot
    store_sequence: int
    observation_group_identity: str
    observation_ids: tuple[ObservationId, ...]
    eligibility: PairSignalCandidateEligibility
    rejection_reason: PairSignalCandidateRejectionReason | None

    def __post_init__(self) -> None:
        self.validate_intrinsic_integrity()

    @property
    def request_id(self) -> str:
        return self.request.request_id

    @property
    def identity_payload(self) -> dict[str, object]:
        return {
            "contract_version": self.contract_version,
            "request_id": self.request_id,
            "role": self.role.value,
            "signal_id": self.signal_snapshot.signal_id.value,
            "signal_content_hash": self.signal_snapshot.signal_content_hash,
            "store_sequence": self.store_sequence,
            "observation_group_identity": self.observation_group_identity,
            "observation_ids": [item.value for item in self.observation_ids],
            "eligibility": self.eligibility.value,
            "rejection_reason": (
                None if self.rejection_reason is None else self.rejection_reason.value
            ),
        }

    @property
    def expected_candidate_id(self) -> str:
        return "pair-signal-candidate-" + digest(self.identity_payload)

    def validate_intrinsic_integrity(self) -> None:
        _require_text(self.candidate_id, "candidate_id")
        if self.contract_version != PAIR_SIGNAL_SELECTION_CANDIDATE_VERSION:
            raise ValueError("unsupported Pair Signal selection candidate")
        self.request.validate_intrinsic_integrity()
        self.signal_snapshot.validate_intrinsic_integrity()
        if not isinstance(self.role, SourceSignalRole):
            raise TypeError("candidate role must be SourceSignalRole")
        _require_positive_int(self.store_sequence, "store_sequence")
        expected_observations = canonical_observation_ids(
            self.signal_snapshot.source_observation_ids
        )
        if self.observation_ids != expected_observations:
            raise ValueError("candidate Observation IDs do not match Signal lineage")
        if self.observation_group_identity != observation_group_identity(
            expected_observations
        ):
            raise ValueError("candidate Observation group identity is not intrinsic")
        expected_reason = _candidate_rejection_reason(
            self.request, self.role, self.signal_snapshot
        )
        expected_eligibility = (
            PairSignalCandidateEligibility.ELIGIBLE
            if expected_reason is None
            else PairSignalCandidateEligibility.INELIGIBLE
        )
        if self.eligibility is not expected_eligibility:
            raise ValueError("candidate eligibility does not match its evidence")
        if self.rejection_reason is not expected_reason:
            raise ValueError("candidate rejection reason does not match its evidence")
        if self.candidate_id != self.expected_candidate_id:
            raise ValueError("candidate_id does not match intrinsic content")


def inspect_source_candidate(
    request: PairSignalMaterializationRequest,
    role: SourceSignalRole,
    signal_snapshot: SignalContentSnapshot,
    store_sequence: int,
) -> PairSignalSelectionCandidate:
    request.validate_intrinsic_integrity()
    signal_snapshot.validate_intrinsic_integrity()
    if not isinstance(role, SourceSignalRole):
        raise TypeError("candidate role must be SourceSignalRole")
    _require_positive_int(store_sequence, "store_sequence")
    observations = canonical_observation_ids(signal_snapshot.source_observation_ids)
    group_identity = observation_group_identity(observations)
    rejection_reason = _candidate_rejection_reason(request, role, signal_snapshot)
    eligibility = (
        PairSignalCandidateEligibility.ELIGIBLE
        if rejection_reason is None
        else PairSignalCandidateEligibility.INELIGIBLE
    )
    payload = {
        "contract_version": PAIR_SIGNAL_SELECTION_CANDIDATE_VERSION,
        "request_id": request.request_id,
        "role": role.value,
        "signal_id": signal_snapshot.signal_id.value,
        "signal_content_hash": signal_snapshot.signal_content_hash,
        "store_sequence": store_sequence,
        "observation_group_identity": group_identity,
        "observation_ids": [item.value for item in observations],
        "eligibility": eligibility.value,
        "rejection_reason": (
            None if rejection_reason is None else rejection_reason.value
        ),
    }
    return PairSignalSelectionCandidate(
        candidate_id="pair-signal-candidate-" + digest(payload),
        contract_version=PAIR_SIGNAL_SELECTION_CANDIDATE_VERSION,
        request=request,
        role=role,
        signal_snapshot=signal_snapshot,
        store_sequence=store_sequence,
        observation_group_identity=group_identity,
        observation_ids=observations,
        eligibility=eligibility,
        rejection_reason=rejection_reason,
    )


@dataclass(frozen=True, slots=True)
class PairSignalSelectionSnapshot:
    selection_snapshot_id: str
    contract_version: str
    request: PairSignalMaterializationRequest
    checkpoint_sequence: int
    captured_at: datetime
    candidates: tuple[PairSignalSelectionCandidate, ...]
    candidate_set_hash: str
    outcome: PairSignalSelectionOutcome
    reason: PairSignalSelectionReason
    selected_base_candidate_id: str | None
    selected_quote_candidate_id: str | None
    selected_base_signal_id: SignalId | None
    selected_quote_signal_id: SignalId | None
    selected_observation_group_identity: str | None

    def __post_init__(self) -> None:
        self.validate_intrinsic_integrity()

    @classmethod
    def create(
        cls,
        *,
        contract_version: str,
        request: PairSignalMaterializationRequest,
        checkpoint_sequence: int,
        captured_at: datetime,
        candidates: Iterable[PairSignalSelectionCandidate],
        outcome: PairSignalSelectionOutcome,
        reason: PairSignalSelectionReason,
        selected_base_candidate_id: str | None = None,
        selected_quote_candidate_id: str | None = None,
        selected_base_signal_id: SignalId | None = None,
        selected_quote_signal_id: SignalId | None = None,
        selected_observation_group_identity: str | None = None,
    ) -> "PairSignalSelectionSnapshot":
        ordered = _canonical_candidates(candidates)
        candidate_set_hash = _candidate_set_hash(ordered)
        payload = _selection_snapshot_payload(
            contract_version=contract_version,
            request=request,
            checkpoint_sequence=checkpoint_sequence,
            candidate_set_hash=candidate_set_hash,
            outcome=outcome,
            reason=reason,
            selected_base_candidate_id=selected_base_candidate_id,
            selected_quote_candidate_id=selected_quote_candidate_id,
            selected_base_signal_id=selected_base_signal_id,
            selected_quote_signal_id=selected_quote_signal_id,
            selected_observation_group_identity=selected_observation_group_identity,
        )
        return cls(
            selection_snapshot_id="pair-signal-selection-" + digest(payload),
            contract_version=contract_version,
            request=request,
            checkpoint_sequence=checkpoint_sequence,
            captured_at=captured_at,
            candidates=ordered,
            candidate_set_hash=candidate_set_hash,
            outcome=outcome,
            reason=reason,
            selected_base_candidate_id=selected_base_candidate_id,
            selected_quote_candidate_id=selected_quote_candidate_id,
            selected_base_signal_id=selected_base_signal_id,
            selected_quote_signal_id=selected_quote_signal_id,
            selected_observation_group_identity=selected_observation_group_identity,
        )

    @property
    def request_id(self) -> str:
        return self.request.request_id

    @property
    def pair(self) -> CurrencyPair:
        return self.request.pair

    @property
    def as_of(self) -> datetime:
        return self.request.as_of

    @property
    def specification_id(self) -> str:
        return self.request.specification.specification_id

    @property
    def identity_payload(self) -> dict[str, object]:
        return _selection_snapshot_payload(
            contract_version=self.contract_version,
            request=self.request,
            checkpoint_sequence=self.checkpoint_sequence,
            candidate_set_hash=self.candidate_set_hash,
            outcome=self.outcome,
            reason=self.reason,
            selected_base_candidate_id=self.selected_base_candidate_id,
            selected_quote_candidate_id=self.selected_quote_candidate_id,
            selected_base_signal_id=self.selected_base_signal_id,
            selected_quote_signal_id=self.selected_quote_signal_id,
            selected_observation_group_identity=(
                self.selected_observation_group_identity
            ),
        )

    @property
    def expected_selection_snapshot_id(self) -> str:
        return "pair-signal-selection-" + digest(self.identity_payload)

    def validate_intrinsic_integrity(self) -> None:
        _require_text(self.selection_snapshot_id, "selection_snapshot_id")
        if self.contract_version != PAIR_SIGNAL_SELECTION_SNAPSHOT_VERSION:
            raise ValueError("unsupported Pair Signal selection snapshot")
        self.request.validate_intrinsic_integrity()
        _require_non_negative_int(self.checkpoint_sequence, "checkpoint_sequence")
        require_utc(self.captured_at, "selection captured_at")
        if self.captured_at < self.as_of:
            raise ValueError("selection captured_at cannot be before request as_of")
        if self.candidates != _canonical_candidates(self.candidates):
            raise ValueError("selection candidates must use canonical ordering")
        candidate_ids = [item.candidate_id for item in self.candidates]
        if len(set(candidate_ids)) != len(candidate_ids):
            raise ValueError("selection candidate IDs must be unique")
        candidate_keys = [
            (item.role, item.signal_snapshot.signal_id, item.store_sequence)
            for item in self.candidates
        ]
        if len(set(candidate_keys)) != len(candidate_keys):
            raise ValueError("selection candidate inventory contains duplicates")
        for candidate in self.candidates:
            candidate.validate_intrinsic_integrity()
            if candidate.request != self.request:
                raise ValueError("selection candidate belongs to another request")
            if candidate.store_sequence > self.checkpoint_sequence:
                raise ValueError("candidate is newer than selection checkpoint")
        if self.candidate_set_hash != _candidate_set_hash(self.candidates):
            raise ValueError("candidate_set_hash does not match candidate inventory")
        self._validate_outcome()
        if self.selection_snapshot_id != self.expected_selection_snapshot_id:
            raise ValueError("selection_snapshot_id does not match intrinsic content")

    def _validate_outcome(self) -> None:
        if not isinstance(self.outcome, PairSignalSelectionOutcome):
            raise TypeError("selection outcome must be PairSignalSelectionOutcome")
        if not isinstance(self.reason, PairSignalSelectionReason):
            raise TypeError("selection reason must be PairSignalSelectionReason")
        if self.outcome is PairSignalSelectionOutcome.SELECTED:
            if self.reason is not PairSignalSelectionReason.SELECTED_EXACT_GROUP:
                raise ValueError("SELECTED requires SELECTED_EXACT_GROUP")
            required = (
                self.selected_base_candidate_id,
                self.selected_quote_candidate_id,
                self.selected_base_signal_id,
                self.selected_quote_signal_id,
                self.selected_observation_group_identity,
            )
            if any(item is None for item in required):
                raise ValueError("SELECTED requires complete selected lineage")
            base, quote = _selected_candidates(self)
            if base.role is not SourceSignalRole.BASE or quote.role is not SourceSignalRole.QUOTE:
                raise ValueError("selected candidates do not match BASE/QUOTE roles")
            if (
                base.eligibility is not PairSignalCandidateEligibility.ELIGIBLE
                or quote.eligibility is not PairSignalCandidateEligibility.ELIGIBLE
            ):
                raise ValueError("SELECTED requires eligible candidates")
            if base.signal_snapshot.signal_id != self.selected_base_signal_id:
                raise ValueError("selected BASE Signal ID does not match candidate")
            if quote.signal_snapshot.signal_id != self.selected_quote_signal_id:
                raise ValueError("selected QUOTE Signal ID does not match candidate")
            if self.selected_base_signal_id == self.selected_quote_signal_id:
                raise ValueError("BASE and QUOTE must use different Signals")
            if (
                base.observation_group_identity != quote.observation_group_identity
                or base.observation_group_identity
                != self.selected_observation_group_identity
            ):
                raise ValueError("selected candidates must share one exact Observation group")
            if base.observation_ids != quote.observation_ids:
                raise ValueError("selected candidates require exact Observation set equality")
            _validate_candidate_role_target(self.request, base)
            _validate_candidate_role_target(self.request, quote)
            if base.signal_snapshot.horizon != quote.signal_snapshot.horizon:
                raise ValueError("selected source Signal Horizons must match")
            return
        selected = (
            self.selected_base_candidate_id,
            self.selected_quote_candidate_id,
            self.selected_base_signal_id,
            self.selected_quote_signal_id,
            self.selected_observation_group_identity,
        )
        if any(item is not None for item in selected):
            raise ValueError("non-selected outcome prohibits selected lineage")
        no_match_reasons = {
            PairSignalSelectionReason.NO_ELIGIBLE_BASE_SIGNAL,
            PairSignalSelectionReason.NO_ELIGIBLE_QUOTE_SIGNAL,
            PairSignalSelectionReason.NO_COMPLETE_OBSERVATION_GROUP,
        }
        ambiguous_reasons = {
            PairSignalSelectionReason.AMBIGUOUS_BASE_SIGNAL,
            PairSignalSelectionReason.AMBIGUOUS_QUOTE_SIGNAL,
            PairSignalSelectionReason.AMBIGUOUS_SOURCE_GROUP,
        }
        if (
            self.outcome is PairSignalSelectionOutcome.NO_MATCH
            and self.reason not in no_match_reasons
        ):
            raise ValueError("NO_MATCH requires a no-match reason")
        if (
            self.outcome is PairSignalSelectionOutcome.AMBIGUOUS
            and self.reason not in ambiguous_reasons
        ):
            raise ValueError("AMBIGUOUS requires an ambiguity reason")


def pair_signal_identity_payload(
    request: PairSignalMaterializationRequest,
    selection_snapshot: PairSignalSelectionSnapshot,
    *,
    materialized_at: datetime,
) -> dict[str, object]:
    request.validate_intrinsic_integrity()
    selection_snapshot.validate_intrinsic_integrity()
    if selection_snapshot.request != request:
        raise ValueError("selection snapshot belongs to another request")
    require_utc(materialized_at, "Pair Signal materialized_at")
    base, quote = _selected_candidates(selection_snapshot)
    if materialized_at < request.as_of:
        raise ValueError("Pair Signal cannot be materialized before request as_of")
    if materialized_at < base.signal_snapshot.created_at:
        raise ValueError("Pair Signal cannot predate BASE Signal")
    if materialized_at < quote.signal_snapshot.created_at:
        raise ValueError("Pair Signal cannot predate QUOTE Signal")
    return {
        "identity_contract_version": PAIR_SIGNAL_IDENTITY_VERSION,
        "materialization_request_id": request.request_id,
        "selection_snapshot_id": selection_snapshot.selection_snapshot_id,
        "pair": request.pair.symbol,
        "output_signal_type": request.specification.output_signal_type,
        "base_signal_id": base.signal_snapshot.signal_id.value,
        "base_signal_content_hash": base.signal_snapshot.signal_content_hash,
        "quote_signal_id": quote.signal_snapshot.signal_id.value,
        "quote_signal_content_hash": quote.signal_snapshot.signal_content_hash,
        "observation_group_identity": (
            selection_snapshot.selected_observation_group_identity
        ),
        "transformation_version": (
            request.specification.output_transformation_version
        ),
        "materialized_at": materialized_at.isoformat(),
    }


def expected_pair_signal_id(
    request: PairSignalMaterializationRequest,
    selection_snapshot: PairSignalSelectionSnapshot,
    *,
    materialized_at: datetime,
) -> SignalId:
    return SignalId(
        "pair-signal-"
        + digest(
            pair_signal_identity_payload(
                request,
                selection_snapshot,
                materialized_at=materialized_at,
            )
        )
    )


@dataclass(frozen=True, slots=True)
class PairSignalDerivation:
    derivation_id: str
    contract_version: str
    pair_signal_id: SignalId
    pair_signal_content_hash: str
    selection_snapshot_id: str
    materialization_request_id: str
    pair: CurrencyPair
    base_candidate_id: str
    base_signal_id: SignalId
    base_signal_content_hash: str
    quote_candidate_id: str
    quote_signal_id: SignalId
    quote_signal_content_hash: str
    observation_group_identity: str
    observation_ids: tuple[ObservationId, ...]
    horizon: Horizon
    transformation_version: str
    specification_id: str
    materialization_request_as_of: datetime
    base_signal_created_at: datetime
    quote_signal_created_at: datetime
    materialized_at: datetime

    def __post_init__(self) -> None:
        self.validate_intrinsic_integrity()

    @classmethod
    def create(
        cls,
        *,
        pair_signal_snapshot: SignalContentSnapshot,
        selection_snapshot: PairSignalSelectionSnapshot,
        materialized_at: datetime,
    ) -> "PairSignalDerivation":
        pair_signal_snapshot.validate_intrinsic_integrity()
        selection_snapshot.validate_intrinsic_integrity()
        request = selection_snapshot.request
        base, quote = _selected_candidates(selection_snapshot)
        expected_id = expected_pair_signal_id(
            request, selection_snapshot, materialized_at=materialized_at
        )
        if pair_signal_snapshot.signal_id != expected_id:
            raise ValueError("Pair Signal ID does not match exact selected lineage")
        if (
            pair_signal_snapshot.target_type is not SignalTargetType.PAIR
            or pair_signal_snapshot.direction_type is not SignalDirectionType.PAIR_SCORE
            or pair_signal_snapshot.target_value != request.pair.symbol
        ):
            raise ValueError("Pair Signal content does not match request Pair")
        if pair_signal_snapshot.signal_type != request.specification.output_signal_type:
            raise ValueError("Pair Signal type does not match specification")
        if pair_signal_snapshot.horizon is not request.specification.horizon:
            raise ValueError("Pair Signal Horizon does not match specification")
        if (
            pair_signal_snapshot.transformation_version
            != request.specification.output_transformation_version
        ):
            raise ValueError("Pair Signal transformation does not match specification")
        if pair_signal_snapshot.created_at != materialized_at:
            raise ValueError("Pair Signal created_at must equal frozen materialized_at")
        expected_features = _canonical_feature_ids(
            base.signal_snapshot.source_feature_ids
            + quote.signal_snapshot.source_feature_ids
        )
        if pair_signal_snapshot.source_feature_ids != expected_features:
            raise ValueError("Pair Signal Feature lineage does not match source Signals")
        if pair_signal_snapshot.source_observation_ids != base.observation_ids:
            raise ValueError("Pair Signal Observation lineage does not match source group")
        values: dict[str, object] = {
            "contract_version": PAIR_SIGNAL_DERIVATION_VERSION,
            "pair_signal_id": pair_signal_snapshot.signal_id,
            "pair_signal_content_hash": pair_signal_snapshot.signal_content_hash,
            "selection_snapshot_id": selection_snapshot.selection_snapshot_id,
            "materialization_request_id": request.request_id,
            "pair": request.pair,
            "base_candidate_id": base.candidate_id,
            "base_signal_id": base.signal_snapshot.signal_id,
            "base_signal_content_hash": base.signal_snapshot.signal_content_hash,
            "quote_candidate_id": quote.candidate_id,
            "quote_signal_id": quote.signal_snapshot.signal_id,
            "quote_signal_content_hash": quote.signal_snapshot.signal_content_hash,
            "observation_group_identity": base.observation_group_identity,
            "observation_ids": base.observation_ids,
            "horizon": request.specification.horizon,
            "transformation_version": request.specification.output_transformation_version,
            "specification_id": request.specification.specification_id,
            "materialization_request_as_of": request.as_of,
            "base_signal_created_at": base.signal_snapshot.created_at,
            "quote_signal_created_at": quote.signal_snapshot.created_at,
            "materialized_at": materialized_at,
        }
        payload = _derivation_payload(**values)  # type: ignore[arg-type]
        return cls(
            derivation_id="pair-signal-derivation-" + digest(payload),
            **values,  # type: ignore[arg-type]
        )

    @property
    def identity_payload(self) -> dict[str, object]:
        return _derivation_payload(
            contract_version=self.contract_version,
            pair_signal_id=self.pair_signal_id,
            pair_signal_content_hash=self.pair_signal_content_hash,
            selection_snapshot_id=self.selection_snapshot_id,
            materialization_request_id=self.materialization_request_id,
            pair=self.pair,
            base_candidate_id=self.base_candidate_id,
            base_signal_id=self.base_signal_id,
            base_signal_content_hash=self.base_signal_content_hash,
            quote_candidate_id=self.quote_candidate_id,
            quote_signal_id=self.quote_signal_id,
            quote_signal_content_hash=self.quote_signal_content_hash,
            observation_group_identity=self.observation_group_identity,
            observation_ids=self.observation_ids,
            horizon=self.horizon,
            transformation_version=self.transformation_version,
            specification_id=self.specification_id,
            materialization_request_as_of=self.materialization_request_as_of,
            base_signal_created_at=self.base_signal_created_at,
            quote_signal_created_at=self.quote_signal_created_at,
            materialized_at=self.materialized_at,
        )

    @property
    def expected_derivation_id(self) -> str:
        return "pair-signal-derivation-" + digest(self.identity_payload)

    def validate_intrinsic_integrity(self) -> None:
        if self.contract_version != PAIR_SIGNAL_DERIVATION_VERSION:
            raise ValueError("unsupported Pair Signal derivation")
        if not isinstance(self.pair_signal_id, SignalId):
            raise TypeError("pair_signal_id must be SignalId")
        if not isinstance(self.base_signal_id, SignalId) or not isinstance(
            self.quote_signal_id, SignalId
        ):
            raise TypeError("source Signal IDs must be SignalId")
        if self.base_signal_id == self.quote_signal_id:
            raise ValueError("BASE and QUOTE source Signal IDs must differ")
        for value, label in (
            (self.derivation_id, "derivation_id"),
            (self.pair_signal_content_hash, "pair_signal_content_hash"),
            (self.selection_snapshot_id, "selection_snapshot_id"),
            (self.materialization_request_id, "materialization_request_id"),
            (self.base_candidate_id, "base_candidate_id"),
            (self.base_signal_content_hash, "base_signal_content_hash"),
            (self.quote_candidate_id, "quote_candidate_id"),
            (self.quote_signal_content_hash, "quote_signal_content_hash"),
            (self.observation_group_identity, "observation_group_identity"),
            (self.specification_id, "specification_id"),
        ):
            _require_text(value, label)
        if not isinstance(self.pair, CurrencyPair):
            raise TypeError("derivation pair must be CurrencyPair")
        if not isinstance(self.horizon, Horizon):
            raise TypeError("derivation horizon must be Horizon")
        if self.transformation_version != SUPPORTED_PAIR_TRANSFORMATION_VERSION:
            raise ValueError("unsupported derivation transformation version")
        if self.observation_ids != canonical_observation_ids(self.observation_ids):
            raise ValueError("derivation Observation IDs must use canonical ordering")
        if self.observation_group_identity != observation_group_identity(
            self.observation_ids
        ):
            raise ValueError("derivation Observation group identity does not match")
        for timestamp, label in (
            (self.materialization_request_as_of, "materialization request as_of"),
            (self.base_signal_created_at, "BASE Signal created_at"),
            (self.quote_signal_created_at, "QUOTE Signal created_at"),
            (self.materialized_at, "Pair Signal materialized_at"),
        ):
            require_utc(timestamp, label)
        if self.materialized_at < self.materialization_request_as_of:
            raise ValueError("derivation materialized_at predates request")
        if self.materialized_at < self.base_signal_created_at:
            raise ValueError("derivation materialized_at predates BASE Signal")
        if self.materialized_at < self.quote_signal_created_at:
            raise ValueError("derivation materialized_at predates QUOTE Signal")
        if self.derivation_id != self.expected_derivation_id:
            raise ValueError("derivation_id does not match intrinsic content")


def _specification_payload(
    *,
    contract_version: str,
    pair: CurrencyPair,
    source_signal_type: str,
    output_signal_type: str,
    horizon: Horizon,
    producer_version: str,
    model_version: str,
    prompt_version: str,
    scorer_version: str,
    expected_source_transformation_version: str | None,
    output_transformation_version: str,
    source_signal_max_age: timedelta,
    observation_group_policy_version: str,
    selection_policy_version: str,
) -> dict[str, object]:
    return {
        "contract_version": contract_version,
        "pair": pair.symbol,
        "source_signal_type": source_signal_type,
        "output_signal_type": output_signal_type,
        "horizon": horizon.value,
        "producer_version": producer_version,
        "model_version": model_version,
        "prompt_version": prompt_version,
        "scorer_version": scorer_version,
        "expected_source_transformation_version": (
            expected_source_transformation_version
        ),
        "output_transformation_version": output_transformation_version,
        "source_signal_max_age_microseconds": _timedelta_microseconds(
            source_signal_max_age
        ),
        "observation_group_policy_version": observation_group_policy_version,
        "selection_policy_version": selection_policy_version,
    }


def _request_payload(
    *,
    contract_version: str,
    pair: CurrencyPair,
    as_of: datetime,
    specification: PairSignalMaterializationSpecification,
) -> dict[str, object]:
    return {
        "contract_version": contract_version,
        "pair": pair.symbol,
        "as_of": as_of.isoformat(),
        "specification_id": specification.specification_id,
        "specification_identity": specification.identity_payload,
    }


def _signal_snapshot_payload(
    *,
    contract_version: str,
    signal_id: SignalId,
    target_type: SignalTargetType,
    target_value: str,
    signal_type: str,
    direction_type: SignalDirectionType,
    direction_value: float,
    strength: float,
    confidence: float,
    horizon: Horizon,
    observed_at: datetime,
    created_at: datetime,
    producer_version: str | None,
    model_version: str | None,
    prompt_version: str | None,
    scorer_version: str | None,
    transformation_version: str | None,
    source_feature_ids: tuple[FeatureId, ...],
    source_observation_ids: tuple[ObservationId, ...],
) -> dict[str, object]:
    return {
        "contract_version": contract_version,
        "signal_id": signal_id.value,
        "target_type": target_type.value,
        "target_value": target_value,
        "signal_type": signal_type,
        "direction_type": direction_type.value,
        "direction_value": direction_value,
        "strength": strength,
        "confidence": confidence,
        "horizon": horizon.value,
        "observed_at": observed_at.isoformat(),
        "created_at": created_at.isoformat(),
        "producer_version": producer_version,
        "model_version": model_version,
        "prompt_version": prompt_version,
        "scorer_version": scorer_version,
        "transformation_version": transformation_version,
        "source_feature_ids": [item.value for item in source_feature_ids],
        "source_observation_ids": [item.value for item in source_observation_ids],
    }


def _candidate_rejection_reason(
    request: PairSignalMaterializationRequest,
    role: SourceSignalRole,
    snapshot: SignalContentSnapshot,
) -> PairSignalCandidateRejectionReason | None:
    specification = request.specification
    if snapshot.target_type is not SignalTargetType.CURRENCY:
        return PairSignalCandidateRejectionReason.TARGET_TYPE_MISMATCH
    expected_currency = request.pair.base if role is SourceSignalRole.BASE else request.pair.quote
    if snapshot.target_value != expected_currency.code:
        return PairSignalCandidateRejectionReason.TARGET_CURRENCY_MISMATCH
    if snapshot.signal_type != specification.source_signal_type:
        return PairSignalCandidateRejectionReason.SIGNAL_TYPE_MISMATCH
    if snapshot.horizon is not specification.horizon:
        return PairSignalCandidateRejectionReason.HORIZON_MISMATCH
    if snapshot.producer_version != specification.producer_version:
        return PairSignalCandidateRejectionReason.PRODUCER_VERSION_MISMATCH
    if snapshot.model_version != specification.model_version:
        return PairSignalCandidateRejectionReason.MODEL_VERSION_MISMATCH
    if snapshot.prompt_version != specification.prompt_version:
        return PairSignalCandidateRejectionReason.PROMPT_VERSION_MISMATCH
    if snapshot.scorer_version != specification.scorer_version:
        return PairSignalCandidateRejectionReason.SCORER_VERSION_MISMATCH
    if (
        snapshot.transformation_version
        != specification.expected_source_transformation_version
    ):
        return PairSignalCandidateRejectionReason.SOURCE_TRANSFORMATION_VERSION_MISMATCH
    if snapshot.direction_type is not SignalDirectionType.DIRECTION_SCORE:
        return PairSignalCandidateRejectionReason.DIRECTION_TYPE_MISMATCH
    if snapshot.created_at > request.as_of:
        return PairSignalCandidateRejectionReason.CREATED_AFTER_AS_OF
    if snapshot.observed_at > request.as_of:
        return PairSignalCandidateRejectionReason.OBSERVED_AFTER_AS_OF
    if request.as_of - snapshot.observed_at > specification.source_signal_max_age:
        return PairSignalCandidateRejectionReason.STALE_AT_AS_OF
    return None


def _validate_candidate_role_target(
    request: PairSignalMaterializationRequest,
    candidate: PairSignalSelectionCandidate,
) -> None:
    expected = (
        request.pair.base if candidate.role is SourceSignalRole.BASE else request.pair.quote
    )
    if (
        candidate.signal_snapshot.target_type is not SignalTargetType.CURRENCY
        or candidate.signal_snapshot.target_value != expected.code
    ):
        raise ValueError("selected candidate target does not match its Pair role")


def _canonical_candidates(
    candidates: Iterable[PairSignalSelectionCandidate],
) -> tuple[PairSignalSelectionCandidate, ...]:
    return tuple(
        sorted(
            candidates,
            key=lambda item: (
                item.role.value,
                item.signal_snapshot.signal_id.value,
                item.store_sequence,
                item.candidate_id,
            ),
        )
    )


def _candidate_set_hash(candidates: tuple[PairSignalSelectionCandidate, ...]) -> str:
    return "candidate-set-" + digest(
        [candidate.identity_payload for candidate in candidates]
    )


def _selection_snapshot_payload(
    *,
    contract_version: str,
    request: PairSignalMaterializationRequest,
    checkpoint_sequence: int,
    candidate_set_hash: str,
    outcome: PairSignalSelectionOutcome,
    reason: PairSignalSelectionReason,
    selected_base_candidate_id: str | None,
    selected_quote_candidate_id: str | None,
    selected_base_signal_id: SignalId | None,
    selected_quote_signal_id: SignalId | None,
    selected_observation_group_identity: str | None,
) -> dict[str, object]:
    return {
        "contract_version": contract_version,
        "request_id": request.request_id,
        "pair": request.pair.symbol,
        "as_of": request.as_of.isoformat(),
        "specification_id": request.specification.specification_id,
        "checkpoint_sequence": checkpoint_sequence,
        "candidate_set_hash": candidate_set_hash,
        "outcome": outcome.value,
        "reason": reason.value,
        "selected_base_candidate_id": selected_base_candidate_id,
        "selected_quote_candidate_id": selected_quote_candidate_id,
        "selected_base_signal_id": (
            None if selected_base_signal_id is None else selected_base_signal_id.value
        ),
        "selected_quote_signal_id": (
            None if selected_quote_signal_id is None else selected_quote_signal_id.value
        ),
        "selected_observation_group_identity": selected_observation_group_identity,
    }


def _selected_candidates(
    snapshot: PairSignalSelectionSnapshot,
) -> tuple[PairSignalSelectionCandidate, PairSignalSelectionCandidate]:
    if snapshot.outcome is not PairSignalSelectionOutcome.SELECTED:
        raise ValueError("Pair Signal identity requires a SELECTED snapshot")
    base = next(
        (
            item
            for item in snapshot.candidates
            if item.candidate_id == snapshot.selected_base_candidate_id
        ),
        None,
    )
    quote = next(
        (
            item
            for item in snapshot.candidates
            if item.candidate_id == snapshot.selected_quote_candidate_id
        ),
        None,
    )
    if base is None or quote is None:
        raise ValueError("selected candidate is absent from inventory")
    return base, quote


def _derivation_payload(
    *,
    contract_version: str,
    pair_signal_id: SignalId,
    pair_signal_content_hash: str,
    selection_snapshot_id: str,
    materialization_request_id: str,
    pair: CurrencyPair,
    base_candidate_id: str,
    base_signal_id: SignalId,
    base_signal_content_hash: str,
    quote_candidate_id: str,
    quote_signal_id: SignalId,
    quote_signal_content_hash: str,
    observation_group_identity: str,
    observation_ids: tuple[ObservationId, ...],
    horizon: Horizon,
    transformation_version: str,
    specification_id: str,
    materialization_request_as_of: datetime,
    base_signal_created_at: datetime,
    quote_signal_created_at: datetime,
    materialized_at: datetime,
) -> dict[str, object]:
    return {
        "contract_version": contract_version,
        "pair_signal_id": pair_signal_id.value,
        "pair_signal_content_hash": pair_signal_content_hash,
        "selection_snapshot_id": selection_snapshot_id,
        "materialization_request_id": materialization_request_id,
        "pair": pair.symbol,
        "base_source": {
            "role": SourceSignalRole.BASE.value,
            "candidate_id": base_candidate_id,
            "signal_id": base_signal_id.value,
            "signal_content_hash": base_signal_content_hash,
        },
        "quote_source": {
            "role": SourceSignalRole.QUOTE.value,
            "candidate_id": quote_candidate_id,
            "signal_id": quote_signal_id.value,
            "signal_content_hash": quote_signal_content_hash,
        },
        "observation_group_identity": observation_group_identity,
        "observation_ids": [item.value for item in observation_ids],
        "horizon": horizon.value,
        "transformation_version": transformation_version,
        "specification_id": specification_id,
        "materialization_request_as_of": materialization_request_as_of.isoformat(),
        "base_signal_created_at": base_signal_created_at.isoformat(),
        "quote_signal_created_at": quote_signal_created_at.isoformat(),
        "materialized_at": materialized_at.isoformat(),
    }


def _canonical_feature_ids(feature_ids: Iterable[FeatureId]) -> tuple[FeatureId, ...]:
    items = tuple(feature_ids)
    if not items:
        raise ValueError("Signal Feature lineage must not be empty")
    if any(not isinstance(item, FeatureId) for item in items):
        raise TypeError("Signal Feature lineage accepts only FeatureId")
    if len({item.value for item in items}) != len(items):
        raise ValueError("Signal Feature lineage IDs must be unique")
    return tuple(sorted(items, key=lambda item: item.value))


def _timedelta_microseconds(value: timedelta) -> int:
    return (
        value.days * 86_400_000_000
        + value.seconds * 1_000_000
        + value.microseconds
    )


def _require_text(value: str, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must not be blank")


def _require_positive_int(value: int, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{label} must be a positive integer")


def _require_non_negative_int(value: int, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
