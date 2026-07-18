import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, fields, replace
from datetime import datetime, timedelta, timezone
from inspect import isfunction, signature
from pathlib import Path
from threading import Barrier

import pytest
from fx_core import FeatureId
from fx_signal_store import (
    PAIR_SIGNAL_MATERIALIZER_RESULT_VERSION,
    OperationalPairSignalMaterializer,
    PairMaterializationPersistenceConflict,
    PairSignalMaterializationClaim,
    PairSignalMaterializationCompletionDisposition,
    PairSignalMaterializationPersistenceResult,
    PairSignalMaterializationRequest,
    PairSignalMaterializationStore,
    PairSignalMaterializerOutcome,
    PairSignalMaterializerResult,
    PairSignalSelectionOutcome,
    PairSignalSelectionPersistenceDisposition,
    PairSignalSelectionPersistenceResult,
    SignalStoreIntegrityError,
    SourceSignalRole,
    SQLiteSignalStore,
    resolve_pair_signal_selection,
)

from tests.factories import feature, observation
from tests.pair_signal_materialization.factories import (
    NOW,
    candidate,
    request,
    source_signal,
    source_snapshot,
)

CLAIMED_AT = NOW + timedelta(minutes=1)
MATERIALIZED_AT = NOW + timedelta(minutes=2)


def _append_source(
    store: SQLiteSignalStore,
    role: SourceSignalRole,
    *,
    identifier: str,
    stored_at: datetime,
    observation_id: str = "observation-1",
    **signal_changes: object,
) -> None:
    feature_id = f"feature-{identifier}"
    store.append_observation_if_absent(observation(observation_id))
    store.append_feature(feature(feature_id, observation_id))
    store.append_signal(
        source_signal(
            role,
            identifier=identifier,
            feature_ids=(FeatureId(feature_id),),
            **signal_changes,
        ),
        stored_at=stored_at,
    )


def _selected_store(path: Path) -> SQLiteSignalStore:
    store = SQLiteSignalStore(path)
    _append_source(
        store,
        SourceSignalRole.BASE,
        identifier="signal-base",
        stored_at=NOW + timedelta(microseconds=1),
    )
    _append_source(
        store,
        SourceSignalRole.QUOTE,
        identifier="signal-quote",
        stored_at=NOW + timedelta(microseconds=2),
    )
    return store


def _outcome_store(
    path: Path,
    outcome: PairSignalSelectionOutcome,
) -> SQLiteSignalStore:
    store = SQLiteSignalStore(path)
    if outcome is PairSignalSelectionOutcome.AMBIGUOUS:
        _append_source(
            store,
            SourceSignalRole.BASE,
            identifier="signal-base-a",
            stored_at=NOW + timedelta(microseconds=1),
        )
        _append_source(
            store,
            SourceSignalRole.BASE,
            identifier="signal-base-b",
            stored_at=NOW + timedelta(microseconds=2),
        )
        _append_source(
            store,
            SourceSignalRole.QUOTE,
            identifier="signal-quote",
            stored_at=NOW + timedelta(microseconds=3),
        )
    return store


def _row_count(path: Path, table: str) -> int:
    with sqlite3.connect(path) as connection:
        row = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    assert row is not None
    return int(row[0])


def _stage_counts(path: Path) -> tuple[int, int, int]:
    return tuple(
        _row_count(path, table)
        for table in (
            "pair_signal_materialization_claims",
            "pair_signal_selection_snapshots",
            "pair_signal_materialization_completions",
        )
    )  # type: ignore[return-value]


def _artifact_counts(path: Path) -> tuple[int, int, int, int]:
    with sqlite3.connect(path) as connection:
        return (
            int(
                connection.execute(
                    "SELECT COUNT(*) FROM signals WHERE id LIKE 'pair-signal-%'"
                ).fetchone()[0]
            ),
            int(
                connection.execute(
                    "SELECT COUNT(*) FROM signal_store_entries "
                    "WHERE storage_origin = 'PAIR_MATERIALIZATION'"
                ).fetchone()[0]
            ),
            int(
                connection.execute(
                    "SELECT COUNT(*) FROM pair_signal_derivations"
                ).fetchone()[0]
            ),
            int(
                connection.execute(
                    "SELECT COUNT(*) FROM pair_signal_materialization_completions"
                ).fetchone()[0]
            ),
        )


class _RecordingStore:
    def __init__(self, store: SQLiteSignalStore) -> None:
        self.store = store
        self.calls: list[str] = []
        self.completion_keyword_arguments: list[dict[str, datetime | None]] = []

    def claim_pair_signal_materialization(
        self,
        item: PairSignalMaterializationRequest,
        *,
        captured_at: datetime,
    ) -> PairSignalMaterializationClaim:
        self.calls.append("claim")
        return self.store.claim_pair_signal_materialization(
            item,
            captured_at=captured_at,
        )

    def capture_pair_signal_selection(
        self,
        item: PairSignalMaterializationRequest,
    ) -> PairSignalSelectionPersistenceResult:
        self.calls.append("selection")
        return self.store.capture_pair_signal_selection(item)

    def complete_pair_signal_materialization(
        self,
        item: PairSignalMaterializationRequest,
        **kwargs: datetime | None,
    ) -> PairSignalMaterializationPersistenceResult:
        self.calls.append("completion")
        self.completion_keyword_arguments.append(dict(kwargs))
        return self.store.complete_pair_signal_materialization(item, **kwargs)


class _CrashAfterStore:
    def __init__(self, store: SQLiteSignalStore, stage: str) -> None:
        self.store = store
        self.stage = stage
        self.calls: list[str] = []

    def _crash(self, stage: str) -> None:
        if self.stage == stage:
            raise RuntimeError(f"crash after {stage}")

    def claim_pair_signal_materialization(
        self,
        item: PairSignalMaterializationRequest,
        *,
        captured_at: datetime,
    ) -> PairSignalMaterializationClaim:
        self.calls.append("claim")
        result = self.store.claim_pair_signal_materialization(
            item,
            captured_at=captured_at,
        )
        self._crash("claim")
        return result

    def capture_pair_signal_selection(
        self,
        item: PairSignalMaterializationRequest,
    ) -> PairSignalSelectionPersistenceResult:
        self.calls.append("selection")
        result = self.store.capture_pair_signal_selection(item)
        self._crash("selection")
        return result

    def complete_pair_signal_materialization(
        self,
        item: PairSignalMaterializationRequest,
        *,
        materialized_at: datetime | None = None,
    ) -> PairSignalMaterializationPersistenceResult:
        self.calls.append("completion")
        result = self.store.complete_pair_signal_materialization(
            item,
            materialized_at=materialized_at,
        )
        self._crash("completion")
        return result


class _FailingStageStore:
    def __init__(
        self,
        store: SQLiteSignalStore,
        stage: str,
        error: Exception,
    ) -> None:
        self.store = store
        self.stage = stage
        self.error = error
        self.calls: list[str] = []

    def _fail(self, stage: str) -> None:
        if self.stage == stage:
            raise self.error

    def claim_pair_signal_materialization(
        self,
        item: PairSignalMaterializationRequest,
        *,
        captured_at: datetime,
    ) -> PairSignalMaterializationClaim:
        self.calls.append("claim")
        self._fail("claim")
        return self.store.claim_pair_signal_materialization(
            item,
            captured_at=captured_at,
        )

    def capture_pair_signal_selection(
        self,
        item: PairSignalMaterializationRequest,
    ) -> PairSignalSelectionPersistenceResult:
        self.calls.append("selection")
        self._fail("selection")
        return self.store.capture_pair_signal_selection(item)

    def complete_pair_signal_materialization(
        self,
        item: PairSignalMaterializationRequest,
        *,
        materialized_at: datetime | None = None,
    ) -> PairSignalMaterializationPersistenceResult:
        self.calls.append("completion")
        self._fail("completion")
        return self.store.complete_pair_signal_materialization(
            item,
            materialized_at=materialized_at,
        )


class _AppendAfterStageStore:
    def __init__(
        self,
        store: SQLiteSignalStore,
        stage: str,
        *,
        backfilled: bool = False,
    ) -> None:
        self.store = store
        self.stage = stage
        self.backfilled = backfilled
        self.appended = False

    def _append(self) -> None:
        if self.appended:
            return
        changes: dict[str, object] = {}
        if self.backfilled:
            changes = {
                "observed_at": NOW - timedelta(days=30),
                "created_at": NOW - timedelta(days=30),
            }
        _append_source(
            self.store,
            SourceSignalRole.BASE,
            identifier="signal-late",
            stored_at=MATERIALIZED_AT + timedelta(minutes=1),
            **changes,
        )
        self.appended = True

    def claim_pair_signal_materialization(
        self,
        item: PairSignalMaterializationRequest,
        *,
        captured_at: datetime,
    ) -> PairSignalMaterializationClaim:
        result = self.store.claim_pair_signal_materialization(
            item,
            captured_at=captured_at,
        )
        if self.stage == "claim":
            self._append()
        return result

    def capture_pair_signal_selection(
        self,
        item: PairSignalMaterializationRequest,
    ) -> PairSignalSelectionPersistenceResult:
        result = self.store.capture_pair_signal_selection(item)
        if self.stage == "selection":
            self._append()
        return result

    def complete_pair_signal_materialization(
        self,
        item: PairSignalMaterializationRequest,
        *,
        materialized_at: datetime | None = None,
    ) -> PairSignalMaterializationPersistenceResult:
        return self.store.complete_pair_signal_materialization(
            item,
            materialized_at=materialized_at,
        )


def _clone_without_validation(instance: object, **changes: object) -> object:
    clone = object.__new__(type(instance))
    for field in fields(instance):  # type: ignore[arg-type]
        value = changes.get(field.name, getattr(instance, field.name))
        object.__setattr__(clone, field.name, value)
    return clone


def test_store_protocol_requires_exactly_three_operations_and_sqlite_conforms(
    tmp_path: Path,
) -> None:
    operations = {
        name
        for name, value in PairSignalMaterializationStore.__dict__.items()
        if not name.startswith("_") and isfunction(value)
    }

    assert operations == {
        "claim_pair_signal_materialization",
        "capture_pair_signal_selection",
        "complete_pair_signal_materialization",
    }
    assert isinstance(
        SQLiteSignalStore(tmp_path / "protocol.sqlite3"),
        PairSignalMaterializationStore,
    )


def test_public_materializer_contract_is_exact_typed_and_frozen(
    tmp_path: Path,
) -> None:
    store = _selected_store(tmp_path / "contract.sqlite3")
    result = OperationalPairSignalMaterializer(store).materialize(
        request(),
        claim_captured_at=CLAIMED_AT,
        materialized_at_if_selected=MATERIALIZED_AT,
    )

    assert tuple(item.value for item in PairSignalMaterializerOutcome) == (
        "MATERIALIZED",
        "REUSED_IDENTICAL",
        "NO_SELECTION",
        "AMBIGUOUS",
    )
    assert tuple(signature(OperationalPairSignalMaterializer.materialize).parameters) == (
        "self",
        "request",
        "claim_captured_at",
        "materialized_at_if_selected",
    )
    assert isinstance(result, PairSignalMaterializerResult)
    assert result.contract_version == PAIR_SIGNAL_MATERIALIZER_RESULT_VERSION
    assert result.selection_snapshot == result.selection_result.selection_snapshot
    assert result.completion == result.completion_result.completion
    assert result.pair_signal_snapshot == result.completion.pair_signal_snapshot
    assert result.selection_reason == result.selection_snapshot.reason
    with pytest.raises(FrozenInstanceError):
        result.outcome = PairSignalMaterializerOutcome.REUSED_IDENTICAL  # type: ignore[misc]


def test_result_rejects_version_request_checkpoint_capture_selection_and_outcome_forgery(
    tmp_path: Path,
) -> None:
    result = OperationalPairSignalMaterializer(
        _selected_store(tmp_path / "result-integrity.sqlite3")
    ).materialize(
        request(),
        claim_captured_at=CLAIMED_AT,
        materialized_at_if_selected=MATERIALIZED_AT,
    )
    with pytest.raises(ValueError, match="unsupported"):
        replace(result, contract_version="pair-signal-materializer-result-v2")
    with pytest.raises(TypeError, match="selection disposition"):
        replace(
            result,
            selection_result=_clone_without_validation(
                result.selection_result,
                disposition="INSERTED",
            ),  # type: ignore[arg-type]
        )
    with pytest.raises(TypeError, match="completion disposition"):
        replace(
            result,
            completion_result=_clone_without_validation(
                result.completion_result,
                disposition="INSERTED",
            ),  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="Claim belongs"):
        replace(result, request=request(as_of=NOW - timedelta(seconds=1)))

    checkpoint_changed = resolve_pair_signal_selection(
        result.request,
        result.claim.checkpoint_sequence + 1,
        result.claim.captured_at,
        result.selection_snapshot.candidates,
    )
    with pytest.raises(ValueError, match="checkpoint"):
        replace(
            result,
            selection_result=PairSignalSelectionPersistenceResult(
                disposition=result.selection_result.disposition,
                selection_snapshot=checkpoint_changed,
            ),
        )

    captured_changed = resolve_pair_signal_selection(
        result.request,
        result.claim.checkpoint_sequence,
        result.claim.captured_at + timedelta(seconds=1),
        result.selection_snapshot.candidates,
    )
    with pytest.raises(ValueError, match="captured_at"):
        replace(
            result,
            selection_result=PairSignalSelectionPersistenceResult(
                disposition=result.selection_result.disposition,
                selection_snapshot=captured_changed,
            ),
        )

    extra = candidate(
        SourceSignalRole.BASE,
        materialization_request=result.request,
        snapshot=source_snapshot(
            SourceSignalRole.BASE,
            identifier="signal-extra",
            scorer_version="scorer-other",
        ),
        store_sequence=result.claim.checkpoint_sequence,
    )
    selection_changed = resolve_pair_signal_selection(
        result.request,
        result.claim.checkpoint_sequence,
        result.claim.captured_at,
        result.selection_snapshot.candidates + (extra,),
    )
    with pytest.raises(ValueError, match="Completion differs"):
        replace(
            result,
            selection_result=PairSignalSelectionPersistenceResult(
                disposition=result.selection_result.disposition,
                selection_snapshot=selection_changed,
            ),
        )
    with pytest.raises(ValueError, match="outcome differs"):
        replace(result, outcome=PairSignalMaterializerOutcome.REUSED_IDENTICAL)


def test_result_rejects_selected_and_non_selected_artifact_cardinality_forgery(
    tmp_path: Path,
) -> None:
    selected = OperationalPairSignalMaterializer(
        _selected_store(tmp_path / "selected-cardinality.sqlite3")
    ).materialize(
        request(),
        claim_captured_at=CLAIMED_AT,
        materialized_at_if_selected=MATERIALIZED_AT,
    )
    forged_selected_completion = _clone_without_validation(
        selected.completion,
        pair_signal_snapshot=None,
    )
    forged_selected_result = _clone_without_validation(
        selected.completion_result,
        completion=forged_selected_completion,
    )
    with pytest.raises(TypeError, match="requires a Pair Signal snapshot"):
        replace(
            selected,
            completion_result=forged_selected_result,  # type: ignore[arg-type]
        )

    no_selection = OperationalPairSignalMaterializer(
        _outcome_store(
            tmp_path / "non-selected-cardinality.sqlite3",
            PairSignalSelectionOutcome.NO_MATCH,
        )
    ).materialize(
        request(),
        claim_captured_at=CLAIMED_AT,
    )
    forged_non_selected_completion = _clone_without_validation(
        no_selection.completion,
        pair_signal_snapshot=selected.pair_signal_snapshot,
    )
    forged_non_selected_result = _clone_without_validation(
        no_selection.completion_result,
        completion=forged_non_selected_completion,
    )
    with pytest.raises(ValueError, match="must not contain Pair artifacts"):
        replace(
            no_selection,
            completion_result=forged_non_selected_result,  # type: ignore[arg-type]
        )


def test_input_validation_happens_before_any_store_operation(tmp_path: Path) -> None:
    recording = _RecordingStore(_selected_store(tmp_path / "inputs.sqlite3"))
    materializer = OperationalPairSignalMaterializer(recording)

    with pytest.raises(TypeError, match="PairSignalMaterializationRequest"):
        materializer.materialize(  # type: ignore[arg-type]
            "request",
            claim_captured_at=CLAIMED_AT,
        )
    with pytest.raises(ValueError, match="before request"):
        materializer.materialize(
            request(),
            claim_captured_at=NOW - timedelta(microseconds=1),
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        materializer.materialize(
            request(),
            claim_captured_at=CLAIMED_AT.replace(tzinfo=None),
        )
    with pytest.raises(ValueError, match="UTC"):
        materializer.materialize(
            request(),
            claim_captured_at=CLAIMED_AT.astimezone(
                timezone(timedelta(hours=9))
            ),
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        materializer.materialize(
            request(),
            claim_captured_at=CLAIMED_AT,
            materialized_at_if_selected=MATERIALIZED_AT.replace(tzinfo=None),
        )
    assert recording.calls == []


def test_first_selected_run_calls_each_stage_once_in_order_and_materializes(
    tmp_path: Path,
) -> None:
    recording = _RecordingStore(_selected_store(tmp_path / "first-selected.sqlite3"))

    result = OperationalPairSignalMaterializer(recording).materialize(
        request(),
        claim_captured_at=CLAIMED_AT,
        materialized_at_if_selected=MATERIALIZED_AT,
    )

    assert recording.calls == ["claim", "selection", "completion"]
    assert recording.completion_keyword_arguments == [
        {"materialized_at": MATERIALIZED_AT}
    ]
    assert result.outcome is PairSignalMaterializerOutcome.MATERIALIZED
    assert result.selection_result.disposition is (
        PairSignalSelectionPersistenceDisposition.INSERTED
    )
    assert result.completion_result.disposition is (
        PairSignalMaterializationCompletionDisposition.INSERTED
    )
    assert result.pair_signal_snapshot is not None
    assert result.completion.pair_signal_store_entry is not None
    assert result.completion.derivation is not None
    assert _stage_counts(recording.store.path) == (1, 1, 1)
    assert _artifact_counts(recording.store.path) == (1, 1, 1, 1)


def test_selected_retry_reauthenticates_first_artifact_without_new_time(
    tmp_path: Path,
) -> None:
    store = _selected_store(tmp_path / "selected-retry.sqlite3")
    materializer = OperationalPairSignalMaterializer(store)
    first = materializer.materialize(
        request(),
        claim_captured_at=CLAIMED_AT,
        materialized_at_if_selected=MATERIALIZED_AT,
    )
    omitted = materializer.materialize(
        request(),
        claim_captured_at=CLAIMED_AT + timedelta(hours=1),
    )
    later = materializer.materialize(
        request(),
        claim_captured_at=CLAIMED_AT + timedelta(hours=2),
        materialized_at_if_selected=MATERIALIZED_AT + timedelta(days=1),
    )

    assert omitted.outcome is PairSignalMaterializerOutcome.REUSED_IDENTICAL
    assert later.outcome is PairSignalMaterializerOutcome.REUSED_IDENTICAL
    assert omitted.selection_result.disposition is (
        PairSignalSelectionPersistenceDisposition.REUSED_IDENTICAL
    )
    assert omitted.completion_result.disposition is (
        PairSignalMaterializationCompletionDisposition.REUSED_IDENTICAL
    )
    assert omitted.claim == first.claim == later.claim
    assert omitted.selection_snapshot == first.selection_snapshot == later.selection_snapshot
    assert omitted.completion == first.completion == later.completion
    assert later.pair_signal_snapshot is not None
    assert later.pair_signal_snapshot.created_at == MATERIALIZED_AT
    assert later.completion.pair_signal_store_entry is not None
    assert later.completion.pair_signal_store_entry.store_sequence == 3
    assert _artifact_counts(store.path) == (1, 1, 1, 1)


@pytest.mark.parametrize(
    ("selection_outcome", "operational_outcome"),
    (
        (
            PairSignalSelectionOutcome.NO_MATCH,
            PairSignalMaterializerOutcome.NO_SELECTION,
        ),
        (
            PairSignalSelectionOutcome.AMBIGUOUS,
            PairSignalMaterializerOutcome.AMBIGUOUS,
        ),
    ),
)
def test_non_selected_outcomes_complete_without_forwarding_conditional_time(
    tmp_path: Path,
    selection_outcome: PairSignalSelectionOutcome,
    operational_outcome: PairSignalMaterializerOutcome,
) -> None:
    recording = _RecordingStore(
        _outcome_store(tmp_path / f"{selection_outcome.value}.sqlite3", selection_outcome)
    )
    materializer = OperationalPairSignalMaterializer(recording)

    first = materializer.materialize(
        request(),
        claim_captured_at=CLAIMED_AT,
        materialized_at_if_selected=MATERIALIZED_AT,
    )
    retried = materializer.materialize(
        request(),
        claim_captured_at=CLAIMED_AT + timedelta(minutes=1),
        materialized_at_if_selected=MATERIALIZED_AT + timedelta(minutes=1),
    )

    assert first.outcome is operational_outcome
    assert retried.outcome is operational_outcome
    assert first.completion_result.disposition is (
        PairSignalMaterializationCompletionDisposition.INSERTED
    )
    assert retried.completion_result.disposition is (
        PairSignalMaterializationCompletionDisposition.REUSED_IDENTICAL
    )
    assert first.completion == retried.completion
    assert first.pair_signal_snapshot is None
    assert first.completion.pair_signal_store_entry is None
    assert first.completion.derivation is None
    assert recording.completion_keyword_arguments == [{}, {}]
    assert _artifact_counts(recording.store.path) == (0, 0, 0, 1)


def test_missing_first_selected_time_leaves_recoverable_claim_and_selection(
    tmp_path: Path,
) -> None:
    store = _selected_store(tmp_path / "missing-time.sqlite3")
    materializer = OperationalPairSignalMaterializer(store)

    with pytest.raises(ValueError, match="requires materialized_at"):
        materializer.materialize(
            request(),
            claim_captured_at=CLAIMED_AT,
        )

    assert _stage_counts(store.path) == (1, 1, 0)
    assert _artifact_counts(store.path) == (0, 0, 0, 0)
    recovered = materializer.materialize(
        request(),
        claim_captured_at=CLAIMED_AT + timedelta(minutes=5),
        materialized_at_if_selected=MATERIALIZED_AT,
    )
    assert recovered.outcome is PairSignalMaterializerOutcome.MATERIALIZED
    assert recovered.selection_result.disposition is (
        PairSignalSelectionPersistenceDisposition.REUSED_IDENTICAL
    )


@pytest.mark.parametrize(
    ("stage", "persisted_counts", "retry_outcome"),
    (
        ("claim", (1, 0, 0), PairSignalMaterializerOutcome.MATERIALIZED),
        ("selection", (1, 1, 0), PairSignalMaterializerOutcome.MATERIALIZED),
        ("completion", (1, 1, 1), PairSignalMaterializerOutcome.REUSED_IDENTICAL),
    ),
)
def test_crash_after_each_durable_stage_converges_by_replaying_request(
    tmp_path: Path,
    stage: str,
    persisted_counts: tuple[int, int, int],
    retry_outcome: PairSignalMaterializerOutcome,
) -> None:
    store = _selected_store(tmp_path / f"crash-{stage}.sqlite3")
    crashing = _CrashAfterStore(store, stage)

    with pytest.raises(RuntimeError, match=f"crash after {stage}"):
        OperationalPairSignalMaterializer(crashing).materialize(
            request(),
            claim_captured_at=CLAIMED_AT,
            materialized_at_if_selected=MATERIALIZED_AT,
        )

    assert _stage_counts(store.path) == persisted_counts
    recovered = OperationalPairSignalMaterializer(store).materialize(
        request(),
        claim_captured_at=CLAIMED_AT + timedelta(minutes=5),
        materialized_at_if_selected=MATERIALIZED_AT + timedelta(minutes=5),
    )
    assert recovered.outcome is retry_outcome
    assert _stage_counts(store.path) == (1, 1, 1)
    assert _artifact_counts(store.path) == (1, 1, 1, 1)


@pytest.mark.parametrize(
    ("stage", "error", "expected_calls"),
    (
        (
            "claim",
            PairMaterializationPersistenceConflict("claim conflict"),
            ["claim"],
        ),
        (
            "selection",
            SignalStoreIntegrityError("selection corrupt"),
            ["claim", "selection"],
        ),
        (
            "completion",
            sqlite3.OperationalError("database is locked"),
            ["claim", "selection", "completion"],
        ),
    ),
)
def test_stage_exception_is_propagated_once_without_retry_or_following_stage(
    tmp_path: Path,
    stage: str,
    error: Exception,
    expected_calls: list[str],
) -> None:
    failing = _FailingStageStore(
        _selected_store(tmp_path / f"failure-{stage}.sqlite3"),
        stage,
        error,
    )

    with pytest.raises(type(error), match=str(error)) as raised:
        OperationalPairSignalMaterializer(failing).materialize(
            request(),
            claim_captured_at=CLAIMED_AT,
            materialized_at_if_selected=MATERIALIZED_AT,
        )

    assert raised.value is error
    assert failing.calls == expected_calls
    assert all(failing.calls.count(item) == 1 for item in failing.calls)


@pytest.mark.parametrize("backfilled", (False, True))
def test_signal_appended_after_claim_is_excluded_by_frozen_checkpoint(
    tmp_path: Path,
    backfilled: bool,
) -> None:
    store = _selected_store(tmp_path / f"late-claim-{backfilled}.sqlite3")
    result = OperationalPairSignalMaterializer(
        _AppendAfterStageStore(store, "claim", backfilled=backfilled)
    ).materialize(
        request(),
        claim_captured_at=CLAIMED_AT,
        materialized_at_if_selected=MATERIALIZED_AT,
    )

    source_ids = {
        item.signal_snapshot.signal_id.value
        for item in result.selection_snapshot.candidates
    }
    assert result.claim.checkpoint_sequence == 2
    assert store.current_signal_checkpoint() == 4
    assert source_ids == {"signal-base", "signal-quote"}
    assert "signal-late" not in source_ids


def test_signal_appended_after_selection_cannot_change_completion_or_retry(
    tmp_path: Path,
) -> None:
    store = _selected_store(tmp_path / "late-selection.sqlite3")
    first = OperationalPairSignalMaterializer(
        _AppendAfterStageStore(store, "selection")
    ).materialize(
        request(),
        claim_captured_at=CLAIMED_AT,
        materialized_at_if_selected=MATERIALIZED_AT,
    )
    retried = OperationalPairSignalMaterializer(store).materialize(
        request(),
        claim_captured_at=CLAIMED_AT + timedelta(minutes=5),
    )

    assert first.selection_snapshot == retried.selection_snapshot
    assert first.completion == retried.completion
    assert retried.outcome is PairSignalMaterializerOutcome.REUSED_IDENTICAL
    assert first.completion.derivation is not None
    assert first.completion.derivation.base_signal_id.value == "signal-base"
    assert first.completion.derivation.quote_signal_id.value == "signal-quote"
    assert _artifact_counts(store.path) == (1, 1, 1, 1)


def test_concurrent_selected_materializers_converge_on_one_exact_artifact(
    tmp_path: Path,
) -> None:
    path = tmp_path / "concurrent-selected.sqlite3"
    _selected_store(path)
    stores = (SQLiteSignalStore(path), SQLiteSignalStore(path))
    barrier = Barrier(2)
    claim_times = (CLAIMED_AT, CLAIMED_AT + timedelta(seconds=1))
    materialized_times = (
        MATERIALIZED_AT + timedelta(minutes=1),
        MATERIALIZED_AT + timedelta(minutes=2),
    )

    def run(index: int) -> PairSignalMaterializerResult:
        barrier.wait()
        return OperationalPairSignalMaterializer(stores[index]).materialize(
            request(),
            claim_captured_at=claim_times[index],
            materialized_at_if_selected=materialized_times[index],
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(run, range(2)))

    assert {item.outcome for item in results} == {
        PairSignalMaterializerOutcome.MATERIALIZED,
        PairSignalMaterializerOutcome.REUSED_IDENTICAL,
    }
    assert {item.completion_result.disposition for item in results} == {
        PairSignalMaterializationCompletionDisposition.INSERTED,
        PairSignalMaterializationCompletionDisposition.REUSED_IDENTICAL,
    }
    assert results[0].claim == results[1].claim
    assert results[0].selection_snapshot == results[1].selection_snapshot
    assert results[0].completion == results[1].completion
    assert results[0].claim.captured_at in claim_times
    assert results[0].pair_signal_snapshot is not None
    assert results[0].pair_signal_snapshot.created_at in materialized_times
    assert _stage_counts(path) == (1, 1, 1)
    assert _artifact_counts(path) == (1, 1, 1, 1)


@pytest.mark.parametrize(
    ("selection_outcome", "operational_outcome"),
    (
        (
            PairSignalSelectionOutcome.NO_MATCH,
            PairSignalMaterializerOutcome.NO_SELECTION,
        ),
        (
            PairSignalSelectionOutcome.AMBIGUOUS,
            PairSignalMaterializerOutcome.AMBIGUOUS,
        ),
    ),
)
def test_concurrent_non_selected_materializers_converge_on_one_completion(
    tmp_path: Path,
    selection_outcome: PairSignalSelectionOutcome,
    operational_outcome: PairSignalMaterializerOutcome,
) -> None:
    path = tmp_path / f"concurrent-{selection_outcome.value}.sqlite3"
    _outcome_store(path, selection_outcome)
    stores = (SQLiteSignalStore(path), SQLiteSignalStore(path))
    barrier = Barrier(2)

    def run(index: int) -> PairSignalMaterializerResult:
        barrier.wait()
        return OperationalPairSignalMaterializer(stores[index]).materialize(
            request(),
            claim_captured_at=CLAIMED_AT + timedelta(seconds=index),
            materialized_at_if_selected=MATERIALIZED_AT,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(run, range(2)))

    assert {item.outcome for item in results} == {operational_outcome}
    assert {item.completion_result.disposition for item in results} == {
        PairSignalMaterializationCompletionDisposition.INSERTED,
        PairSignalMaterializationCompletionDisposition.REUSED_IDENTICAL,
    }
    assert results[0].claim == results[1].claim
    assert results[0].selection_snapshot == results[1].selection_snapshot
    assert results[0].completion == results[1].completion
    assert _stage_counts(path) == (1, 1, 1)
    assert _artifact_counts(path) == (0, 0, 0, 1)
