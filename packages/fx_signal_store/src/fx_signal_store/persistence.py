from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from fx_core import FeatureId, ObservationId, SignalId
from fx_core.time import require_utc

if TYPE_CHECKING:
    from .pair_materialization import (
        PairSignalMaterializationRequest,
        PairSignalSelectionSnapshot,
    )


SIGNAL_STORE_ENTRY_VERSION = "signal-store-entry-v1"
PAIR_SIGNAL_MATERIALIZATION_CLAIM_VERSION = "pair-signal-materialization-claim-v1"


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


def _require_positive_int(value: int, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{label} must be a positive integer")


def _require_non_negative_int(value: int, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
