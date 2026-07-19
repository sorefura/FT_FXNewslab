from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol, cast, runtime_checkable

from fx_core.time import require_utc

from .pair_materialization import (
    PairSignalDerivation,
    PairSignalMaterializationRequest,
    PairSignalMaterializationSpecification,
    PairSignalSelectionCandidate,
    PairSignalSelectionOutcome,
    PairSignalSelectionReason,
    PairSignalSelectionSnapshot,
    SignalContentSnapshot,
)
from .persistence import (
    PairSignalMaterializationClaim,
    PairSignalMaterializationCompletion,
    PairSignalMaterializationCompletionDisposition,
    PairSignalMaterializationPersistenceResult,
    PairSignalSelectionPersistenceDisposition,
    PairSignalSelectionPersistenceResult,
    SignalStoreEntry,
    SignalStoreIntegrityError,
)

PAIR_SIGNAL_MATERIALIZER_RESULT_VERSION = "pair-signal-materializer-result-v1"


@runtime_checkable
class PairSignalMaterializationStore(Protocol):
    def claim_pair_signal_materialization(
        self,
        request: PairSignalMaterializationRequest,
        *,
        captured_at: datetime,
    ) -> PairSignalMaterializationClaim: ...

    def capture_pair_signal_selection(
        self,
        request: PairSignalMaterializationRequest,
    ) -> PairSignalSelectionPersistenceResult: ...

    def complete_pair_signal_materialization(
        self,
        request: PairSignalMaterializationRequest,
        *,
        materialized_at: datetime | None = None,
    ) -> PairSignalMaterializationPersistenceResult: ...


class PairSignalMaterializerOutcome(StrEnum):
    MATERIALIZED = "MATERIALIZED"
    REUSED_IDENTICAL = "REUSED_IDENTICAL"
    NO_SELECTION = "NO_SELECTION"
    AMBIGUOUS = "AMBIGUOUS"


@dataclass(frozen=True, slots=True)
class PairSignalMaterializerResult:
    contract_version: str
    request: PairSignalMaterializationRequest
    outcome: PairSignalMaterializerOutcome
    claim: PairSignalMaterializationClaim
    selection_result: PairSignalSelectionPersistenceResult
    completion_result: PairSignalMaterializationPersistenceResult

    def __post_init__(self) -> None:
        PairSignalMaterializerResult.validate_intrinsic_integrity(self)

    @property
    def selection_snapshot(self) -> PairSignalSelectionSnapshot:
        return self.selection_result.selection_snapshot

    @property
    def completion(self) -> PairSignalMaterializationCompletion:
        return self.completion_result.completion

    @property
    def pair_signal_snapshot(self) -> SignalContentSnapshot | None:
        return self.completion.pair_signal_snapshot

    @property
    def selection_reason(self) -> PairSignalSelectionReason:
        return self.selection_snapshot.reason

    def validate_intrinsic_integrity(self) -> None:
        _require_exact_type(
            self,
            PairSignalMaterializerResult,
            "materializer result",
        )
        if self.contract_version != PAIR_SIGNAL_MATERIALIZER_RESULT_VERSION:
            raise ValueError("unsupported Pair Signal materializer result")
        request = _validate_request_contract(self.request)
        claim = _validate_claim_contract(self.claim)
        if claim.request != request:
            raise ValueError("materializer Claim belongs to another Request")
        selection_result = _validate_selection_result_contract(self.selection_result)
        selection = selection_result.selection_snapshot
        if selection.request != request:
            raise ValueError("materializer Selection belongs to another Request")
        if selection.checkpoint_sequence != claim.checkpoint_sequence:
            raise ValueError("materializer Selection checkpoint differs from Claim")
        if selection.captured_at != claim.captured_at:
            raise ValueError("materializer Selection captured_at differs from Claim")
        completion_result = _validate_completion_result_contract(
            self.completion_result
        )
        completion = completion_result.completion
        if completion.request != request:
            raise ValueError("materializer Completion belongs to another Request")
        if completion.selection_snapshot != selection:
            raise ValueError("materializer Completion differs from Selection")
        if completion.outcome is not selection.outcome:
            raise ValueError("materializer Completion outcome differs from Selection")
        if type(self.outcome) is not PairSignalMaterializerOutcome:
            raise TypeError("outcome must be PairSignalMaterializerOutcome")
        expected_outcome = _operational_outcome(
            selection.outcome,
            completion_result.disposition,
        )
        if self.outcome is not expected_outcome:
            raise ValueError("materializer outcome differs from persisted evidence")


@dataclass(frozen=True, slots=True)
class OperationalPairSignalMaterializer:
    store: PairSignalMaterializationStore

    def __post_init__(self) -> None:
        if not isinstance(self.store, PairSignalMaterializationStore):
            raise TypeError("store must implement PairSignalMaterializationStore")

    def materialize(
        self,
        request: PairSignalMaterializationRequest,
        *,
        claim_captured_at: datetime,
        materialized_at_if_selected: datetime | None = None,
    ) -> PairSignalMaterializerResult:
        if not isinstance(request, PairSignalMaterializationRequest):
            raise TypeError("request must be PairSignalMaterializationRequest")
        try:
            request = _validate_request_contract(request)
        except (AttributeError, TypeError, ValueError) as error:
            raise SignalStoreIntegrityError(
                "materializer supplied Request is invalid evidence"
            ) from error
        require_utc(claim_captured_at, "materialization claim captured_at")
        if claim_captured_at < request.as_of:
            raise ValueError("materialization claim captured_at cannot be before request as_of")
        if materialized_at_if_selected is not None:
            require_utc(
                materialized_at_if_selected,
                "Pair Signal materialized_at",
            )

        claim = self.store.claim_pair_signal_materialization(
            request,
            captured_at=claim_captured_at,
        )
        claim = _validate_claim_stage(request, claim)
        selection_result = _validate_selection_stage(
            request,
            claim,
            self.store.capture_pair_signal_selection(request),
        )
        if (
            selection_result.selection_snapshot.outcome
            is PairSignalSelectionOutcome.SELECTED
        ):
            completion_result = self.store.complete_pair_signal_materialization(
                request,
                materialized_at=materialized_at_if_selected,
            )
        else:
            completion_result = self.store.complete_pair_signal_materialization(request)
        completion_result = _validate_completion_stage(
            request,
            selection_result,
            completion_result,
        )
        outcome = _operational_outcome(
            selection_result.selection_snapshot.outcome,
            completion_result.disposition,
        )
        return PairSignalMaterializerResult(
            contract_version=PAIR_SIGNAL_MATERIALIZER_RESULT_VERSION,
            request=request,
            outcome=outcome,
            claim=claim,
            selection_result=selection_result,
            completion_result=completion_result,
        )


def _validate_claim_stage(
    request: PairSignalMaterializationRequest,
    claim: object,
) -> PairSignalMaterializationClaim:
    try:
        claim = _validate_claim_contract(claim)
        if claim.request != request:
            raise ValueError("Claim belongs to another materialization Request")
    except (AttributeError, TypeError, ValueError) as error:
        raise SignalStoreIntegrityError(
            "materializer Claim stage returned invalid evidence"
        ) from error
    return claim


def _validate_selection_stage(
    request: PairSignalMaterializationRequest,
    claim: PairSignalMaterializationClaim,
    selection_result: object,
) -> PairSignalSelectionPersistenceResult:
    try:
        validated = _validate_selection_result_contract(selection_result)
        selection = validated.selection_snapshot
        if selection.request != request:
            raise ValueError("Selection belongs to another materialization Request")
        if selection.checkpoint_sequence != claim.checkpoint_sequence:
            raise ValueError("Selection checkpoint differs from Claim")
        if selection.captured_at != claim.captured_at:
            raise ValueError("Selection captured_at differs from Claim")
    except (AttributeError, TypeError, ValueError) as error:
        raise SignalStoreIntegrityError(
            "materializer Selection stage returned invalid evidence"
        ) from error
    return validated


def _validate_completion_stage(
    request: PairSignalMaterializationRequest,
    selection_result: PairSignalSelectionPersistenceResult,
    completion_result: object,
) -> PairSignalMaterializationPersistenceResult:
    try:
        validated = _validate_completion_result_contract(completion_result)
        completion = validated.completion
        if completion.request != request:
            raise ValueError("Completion belongs to another materialization Request")
        if completion.selection_snapshot != selection_result.selection_snapshot:
            raise ValueError("Completion belongs to another Selection")
        if completion.outcome is not selection_result.selection_snapshot.outcome:
            raise ValueError("Completion outcome differs from Selection")
    except (AttributeError, TypeError, ValueError) as error:
        raise SignalStoreIntegrityError(
            "materializer Completion stage returned invalid evidence"
        ) from error
    return validated


def _require_exact_type(
    value: object,
    expected_type: type[object],
    label: str,
) -> None:
    if type(value) is not expected_type:
        raise TypeError(f"{label} must use the exact supported contract type")


def _validate_request_contract(
    value: object,
) -> PairSignalMaterializationRequest:
    _require_exact_type(value, PairSignalMaterializationRequest, "Request")
    request = cast(PairSignalMaterializationRequest, value)
    _require_exact_type(
        request.specification,
        PairSignalMaterializationSpecification,
        "Specification",
    )
    specification = request.specification
    PairSignalMaterializationSpecification.validate_intrinsic_integrity(
        specification
    )
    PairSignalMaterializationRequest.validate_intrinsic_integrity(request)
    return request


def _validate_claim_contract(value: object) -> PairSignalMaterializationClaim:
    _require_exact_type(value, PairSignalMaterializationClaim, "Claim")
    claim = cast(PairSignalMaterializationClaim, value)
    _validate_request_contract(claim.request)
    PairSignalMaterializationClaim.validate_intrinsic_integrity(claim)
    return claim


def _validate_signal_snapshot_contract(value: object) -> SignalContentSnapshot:
    _require_exact_type(value, SignalContentSnapshot, "Signal Snapshot")
    snapshot = cast(SignalContentSnapshot, value)
    SignalContentSnapshot.validate_intrinsic_integrity(snapshot)
    return snapshot


def _validate_candidate_contract(value: object) -> PairSignalSelectionCandidate:
    _require_exact_type(value, PairSignalSelectionCandidate, "Selection Candidate")
    candidate = cast(PairSignalSelectionCandidate, value)
    _validate_request_contract(candidate.request)
    _validate_signal_snapshot_contract(candidate.signal_snapshot)
    PairSignalSelectionCandidate.validate_intrinsic_integrity(candidate)
    return candidate


def _validate_selection_snapshot_contract(
    value: object,
) -> PairSignalSelectionSnapshot:
    _require_exact_type(value, PairSignalSelectionSnapshot, "Selection Snapshot")
    snapshot = cast(PairSignalSelectionSnapshot, value)
    _validate_request_contract(snapshot.request)
    for candidate in snapshot.candidates:
        _validate_candidate_contract(candidate)
    PairSignalSelectionSnapshot.validate_intrinsic_integrity(snapshot)
    return snapshot


def _validate_selection_result_contract(
    value: object,
) -> PairSignalSelectionPersistenceResult:
    _require_exact_type(
        value,
        PairSignalSelectionPersistenceResult,
        "Selection result",
    )
    result = cast(PairSignalSelectionPersistenceResult, value)
    _require_exact_type(
        result.disposition,
        PairSignalSelectionPersistenceDisposition,
        "selection disposition",
    )
    snapshot = _validate_selection_snapshot_contract(result.selection_snapshot)
    validated = PairSignalSelectionPersistenceResult(
        disposition=result.disposition,
        selection_snapshot=snapshot,
    )
    if validated != result:
        raise ValueError("Selection result differs after intrinsic validation")
    return validated


def _validate_store_entry_contract(value: object) -> SignalStoreEntry:
    _require_exact_type(value, SignalStoreEntry, "Signal Store entry")
    entry = cast(SignalStoreEntry, value)
    validated = SignalStoreEntry(
        contract_version=entry.contract_version,
        store_sequence=entry.store_sequence,
        signal_id=entry.signal_id,
        stored_at=entry.stored_at,
        storage_origin=entry.storage_origin,
    )
    if validated != entry:
        raise ValueError("Signal Store entry differs after intrinsic validation")
    return validated


def _validate_derivation_contract(value: object) -> PairSignalDerivation:
    _require_exact_type(value, PairSignalDerivation, "Pair Signal Derivation")
    derivation = cast(PairSignalDerivation, value)
    PairSignalDerivation.validate_intrinsic_integrity(derivation)
    return derivation


def _validate_completion_contract(
    value: object,
) -> PairSignalMaterializationCompletion:
    _require_exact_type(
        value,
        PairSignalMaterializationCompletion,
        "Completion",
    )
    completion = cast(PairSignalMaterializationCompletion, value)
    _validate_request_contract(completion.request)
    _validate_selection_snapshot_contract(completion.selection_snapshot)
    if completion.pair_signal_snapshot is not None:
        _validate_signal_snapshot_contract(completion.pair_signal_snapshot)
    if completion.pair_signal_store_entry is not None:
        _validate_store_entry_contract(completion.pair_signal_store_entry)
    if completion.derivation is not None:
        _validate_derivation_contract(completion.derivation)
    PairSignalMaterializationCompletion.validate_intrinsic_integrity(completion)
    return completion


def _validate_completion_result_contract(
    value: object,
) -> PairSignalMaterializationPersistenceResult:
    _require_exact_type(
        value,
        PairSignalMaterializationPersistenceResult,
        "Completion result",
    )
    result = cast(PairSignalMaterializationPersistenceResult, value)
    _require_exact_type(
        result.disposition,
        PairSignalMaterializationCompletionDisposition,
        "completion disposition",
    )
    completion = _validate_completion_contract(result.completion)
    validated = PairSignalMaterializationPersistenceResult(
        disposition=result.disposition,
        completion=completion,
    )
    if validated != result:
        raise ValueError("Completion result differs after intrinsic validation")
    return validated


def _operational_outcome(
    selection_outcome: PairSignalSelectionOutcome,
    completion_disposition: PairSignalMaterializationCompletionDisposition,
) -> PairSignalMaterializerOutcome:
    if selection_outcome is PairSignalSelectionOutcome.SELECTED:
        if (
            completion_disposition
            is PairSignalMaterializationCompletionDisposition.INSERTED
        ):
            return PairSignalMaterializerOutcome.MATERIALIZED
        if (
            completion_disposition
            is PairSignalMaterializationCompletionDisposition.REUSED_IDENTICAL
        ):
            return PairSignalMaterializerOutcome.REUSED_IDENTICAL
        raise ValueError("unsupported SELECTED completion disposition")
    if selection_outcome is PairSignalSelectionOutcome.NO_MATCH:
        return PairSignalMaterializerOutcome.NO_SELECTION
    if selection_outcome is PairSignalSelectionOutcome.AMBIGUOUS:
        return PairSignalMaterializerOutcome.AMBIGUOUS
    raise ValueError("unsupported Pair Signal selection outcome")
