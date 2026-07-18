from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable

from fx_core.time import require_utc

from .pair_materialization import (
    PairSignalMaterializationRequest,
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
        self.validate_intrinsic_integrity()

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
        if self.contract_version != PAIR_SIGNAL_MATERIALIZER_RESULT_VERSION:
            raise ValueError("unsupported Pair Signal materializer result")
        if not isinstance(self.request, PairSignalMaterializationRequest):
            raise TypeError("request must be PairSignalMaterializationRequest")
        self.request.validate_intrinsic_integrity()
        if not isinstance(self.claim, PairSignalMaterializationClaim):
            raise TypeError("claim must be PairSignalMaterializationClaim")
        self.claim.validate_intrinsic_integrity()
        if self.claim.request != self.request:
            raise ValueError("materializer Claim belongs to another Request")
        if not isinstance(
            self.selection_result,
            PairSignalSelectionPersistenceResult,
        ):
            raise TypeError(
                "selection_result must be PairSignalSelectionPersistenceResult"
            )
        self.selection_snapshot.validate_intrinsic_integrity()
        if not isinstance(
            self.selection_result.disposition,
            PairSignalSelectionPersistenceDisposition,
        ):
            raise TypeError("selection disposition is invalid")
        if self.selection_snapshot.request != self.request:
            raise ValueError("materializer Selection belongs to another Request")
        if self.selection_snapshot.checkpoint_sequence != self.claim.checkpoint_sequence:
            raise ValueError("materializer Selection checkpoint differs from Claim")
        if self.selection_snapshot.captured_at != self.claim.captured_at:
            raise ValueError("materializer Selection captured_at differs from Claim")
        if not isinstance(
            self.completion_result,
            PairSignalMaterializationPersistenceResult,
        ):
            raise TypeError(
                "completion_result must be PairSignalMaterializationPersistenceResult"
            )
        self.completion.validate_intrinsic_integrity()
        if not isinstance(
            self.completion_result.disposition,
            PairSignalMaterializationCompletionDisposition,
        ):
            raise TypeError("completion disposition is invalid")
        if self.completion.request != self.request:
            raise ValueError("materializer Completion belongs to another Request")
        if self.completion.selection_snapshot != self.selection_snapshot:
            raise ValueError("materializer Completion differs from Selection")
        if self.completion.outcome is not self.selection_snapshot.outcome:
            raise ValueError("materializer Completion outcome differs from Selection")
        if not isinstance(self.outcome, PairSignalMaterializerOutcome):
            raise TypeError("outcome must be PairSignalMaterializerOutcome")
        expected_outcome = _operational_outcome(
            self.selection_snapshot.outcome,
            self.completion_result.disposition,
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
        request.validate_intrinsic_integrity()
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
        if not isinstance(claim, PairSignalMaterializationClaim):
            raise TypeError("Claim result must be PairSignalMaterializationClaim")
        claim.validate_intrinsic_integrity()
        if claim.request != request:
            raise ValueError("Claim belongs to another materialization Request")
    except (TypeError, ValueError) as error:
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
        if not isinstance(selection_result, PairSignalSelectionPersistenceResult):
            raise TypeError(
                "Selection result must be PairSignalSelectionPersistenceResult"
            )
        validated = PairSignalSelectionPersistenceResult(
            disposition=selection_result.disposition,
            selection_snapshot=selection_result.selection_snapshot,
        )
        if validated != selection_result:
            raise ValueError("Selection result differs after intrinsic validation")
        selection = validated.selection_snapshot
        if selection.request != request:
            raise ValueError("Selection belongs to another materialization Request")
        if selection.checkpoint_sequence != claim.checkpoint_sequence:
            raise ValueError("Selection checkpoint differs from Claim")
        if selection.captured_at != claim.captured_at:
            raise ValueError("Selection captured_at differs from Claim")
    except (TypeError, ValueError) as error:
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
        if not isinstance(
            completion_result,
            PairSignalMaterializationPersistenceResult,
        ):
            raise TypeError(
                "Completion result must be PairSignalMaterializationPersistenceResult"
            )
        validated = PairSignalMaterializationPersistenceResult(
            disposition=completion_result.disposition,
            completion=completion_result.completion,
        )
        if validated != completion_result:
            raise ValueError("Completion result differs after intrinsic validation")
        completion = validated.completion
        if completion.request != request:
            raise ValueError("Completion belongs to another materialization Request")
        if completion.selection_snapshot != selection_result.selection_snapshot:
            raise ValueError("Completion belongs to another Selection")
        if completion.outcome is not selection_result.selection_snapshot.outcome:
            raise ValueError("Completion outcome differs from Selection")
    except (TypeError, ValueError) as error:
        raise SignalStoreIntegrityError(
            "materializer Completion stage returned invalid evidence"
        ) from error
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
