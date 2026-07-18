from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from fx_core import FeatureId, ObservationId, SignalId
from fx_core.time import require_utc

if TYPE_CHECKING:
    from .pair_materialization import (
        PairSignalDerivation,
        PairSignalMaterializationRequest,
        PairSignalSelectionOutcome,
        PairSignalSelectionSnapshot,
        SignalContentSnapshot,
    )


SIGNAL_STORE_ENTRY_VERSION = "signal-store-entry-v1"
PAIR_SIGNAL_MATERIALIZATION_CLAIM_VERSION = "pair-signal-materialization-claim-v1"
PAIR_SIGNAL_MATERIALIZATION_COMPLETION_VERSION = (
    "pair-signal-materialization-completion-v1"
)


class PairMaterializationPersistenceConflict(ValueError):
    pass


class SignalStoreIntegrityError(RuntimeError):
    pass


class SignalStorageOrigin(StrEnum):
    LEGACY_BACKFILL = "LEGACY_BACKFILL"
    APPEND = "APPEND"
    PAIR_MATERIALIZATION = "PAIR_MATERIALIZATION"


class PairSignalSelectionPersistenceDisposition(StrEnum):
    INSERTED = "INSERTED"
    REUSED_IDENTICAL = "REUSED_IDENTICAL"


class PairSignalMaterializationCompletionDisposition(StrEnum):
    INSERTED = "INSERTED"
    REUSED_IDENTICAL = "REUSED_IDENTICAL"


@dataclass(frozen=True, slots=True)
class SignalLineage:
    signal_id: SignalId
    feature_ids: tuple[FeatureId, ...]
    observation_ids: tuple[ObservationId, ...]


@dataclass(frozen=True, slots=True)
class SignalStoreEntry:
    contract_version: str
    store_sequence: int
    signal_id: SignalId
    stored_at: datetime
    storage_origin: SignalStorageOrigin

    def __post_init__(self) -> None:
        if self.contract_version != SIGNAL_STORE_ENTRY_VERSION:
            raise ValueError("unsupported Signal Store entry contract")
        _require_positive_int(self.store_sequence, "store_sequence")
        if not isinstance(self.signal_id, SignalId):
            raise TypeError("signal_id must be SignalId")
        require_utc(self.stored_at, "Signal Store entry stored_at")
        if not isinstance(self.storage_origin, SignalStorageOrigin):
            raise TypeError("storage_origin must be SignalStorageOrigin")


@dataclass(frozen=True, slots=True)
class PairSignalMaterializationClaim:
    contract_version: str
    request: PairSignalMaterializationRequest
    checkpoint_sequence: int
    captured_at: datetime

    def __post_init__(self) -> None:
        self.validate_intrinsic_integrity()

    def validate_intrinsic_integrity(self) -> None:
        from .pair_materialization import PairSignalMaterializationRequest

        if self.contract_version != PAIR_SIGNAL_MATERIALIZATION_CLAIM_VERSION:
            raise ValueError("unsupported Pair Signal materialization claim")
        if not isinstance(self.request, PairSignalMaterializationRequest):
            raise TypeError("request must be PairSignalMaterializationRequest")
        self.request.validate_intrinsic_integrity()
        _require_non_negative_int(self.checkpoint_sequence, "checkpoint_sequence")
        require_utc(self.captured_at, "materialization claim captured_at")
        if self.captured_at < self.request.as_of:
            raise ValueError("materialization claim captured_at cannot be before request as_of")


@dataclass(frozen=True, slots=True)
class PairSignalSelectionPersistenceResult:
    disposition: PairSignalSelectionPersistenceDisposition
    selection_snapshot: PairSignalSelectionSnapshot

    def __post_init__(self) -> None:
        from .pair_materialization import PairSignalSelectionSnapshot

        if not isinstance(
            self.disposition, PairSignalSelectionPersistenceDisposition
        ):
            raise TypeError(
                "disposition must be PairSignalSelectionPersistenceDisposition"
            )
        if not isinstance(self.selection_snapshot, PairSignalSelectionSnapshot):
            raise TypeError("selection_snapshot must be PairSignalSelectionSnapshot")
        self.selection_snapshot.validate_intrinsic_integrity()


@dataclass(frozen=True, slots=True)
class PairSignalMaterializationCompletion:
    contract_version: str
    request: PairSignalMaterializationRequest
    selection_snapshot: PairSignalSelectionSnapshot
    outcome: PairSignalSelectionOutcome
    pair_signal_snapshot: SignalContentSnapshot | None
    pair_signal_store_entry: SignalStoreEntry | None
    derivation: PairSignalDerivation | None

    def __post_init__(self) -> None:
        self.validate_intrinsic_integrity()

    def validate_intrinsic_integrity(self) -> None:
        from .pair_materialization import (
            PairSignalDerivation,
            PairSignalMaterializationRequest,
            PairSignalSelectionOutcome,
            PairSignalSelectionSnapshot,
            SignalContentSnapshot,
            SignalTargetType,
        )

        if self.contract_version != PAIR_SIGNAL_MATERIALIZATION_COMPLETION_VERSION:
            raise ValueError("unsupported Pair Signal materialization completion")
        if not isinstance(self.request, PairSignalMaterializationRequest):
            raise TypeError("request must be PairSignalMaterializationRequest")
        if not isinstance(self.selection_snapshot, PairSignalSelectionSnapshot):
            raise TypeError("selection_snapshot must be PairSignalSelectionSnapshot")
        self.request.validate_intrinsic_integrity()
        self.selection_snapshot.validate_intrinsic_integrity()
        if self.selection_snapshot.request != self.request:
            raise ValueError("selection Snapshot belongs to another Request")
        if not isinstance(self.outcome, PairSignalSelectionOutcome):
            raise TypeError("outcome must be PairSignalSelectionOutcome")
        if self.outcome is not self.selection_snapshot.outcome:
            raise ValueError("completion outcome differs from Selection")
        if self.outcome is PairSignalSelectionOutcome.SELECTED:
            if not isinstance(self.pair_signal_snapshot, SignalContentSnapshot):
                raise TypeError("SELECTED completion requires a Pair Signal snapshot")
            if not isinstance(self.pair_signal_store_entry, SignalStoreEntry):
                raise TypeError("SELECTED completion requires a Signal Store entry")
            if not isinstance(self.derivation, PairSignalDerivation):
                raise TypeError("SELECTED completion requires a Pair Signal derivation")
            self.pair_signal_snapshot.validate_intrinsic_integrity()
            if (
                self.pair_signal_snapshot.target_type is not SignalTargetType.PAIR
                or self.pair_signal_snapshot.target_value != self.request.pair.symbol
            ):
                raise ValueError("Pair Signal snapshot does not target the exact Pair")
            if (
                self.pair_signal_store_entry.signal_id
                != self.pair_signal_snapshot.signal_id
            ):
                raise ValueError("Signal Store entry belongs to another Signal")
            if (
                self.pair_signal_store_entry.storage_origin
                is not SignalStorageOrigin.PAIR_MATERIALIZATION
            ):
                raise ValueError("Pair Signal Store entry has the wrong origin")
            if (
                self.pair_signal_store_entry.stored_at
                != self.pair_signal_snapshot.created_at
            ):
                raise ValueError("Pair Signal Store time differs from materialization time")
            self.derivation.validate_against(
                self.pair_signal_snapshot,
                self.selection_snapshot,
            )
        elif any(
            artifact is not None
            for artifact in (
                self.pair_signal_snapshot,
                self.pair_signal_store_entry,
                self.derivation,
            )
        ):
            raise ValueError("non-selected completion must not contain Pair artifacts")


@dataclass(frozen=True, slots=True)
class PairSignalMaterializationPersistenceResult:
    disposition: PairSignalMaterializationCompletionDisposition
    completion: PairSignalMaterializationCompletion

    def __post_init__(self) -> None:
        if not isinstance(
            self.disposition,
            PairSignalMaterializationCompletionDisposition,
        ):
            raise TypeError(
                "disposition must be PairSignalMaterializationCompletionDisposition"
            )
        if not isinstance(self.completion, PairSignalMaterializationCompletion):
            raise TypeError("completion must be PairSignalMaterializationCompletion")
        self.completion.validate_intrinsic_integrity()


def _require_positive_int(value: int, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{label} must be a positive integer")


def _require_non_negative_int(value: int, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
