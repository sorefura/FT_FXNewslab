import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from datetime import timedelta, timezone
from inspect import signature
from pathlib import Path
from threading import Barrier

import pytest
from fx_core import FeatureId
from fx_signal_store import (
    PAIR_SIGNAL_MATERIALIZATION_COMPLETION_VERSION,
    PairMaterializationPersistenceConflict,
    PairSignalDerivation,
    PairSignalMaterializationCompletion,
    PairSignalMaterializationCompletionDisposition,
    PairSignalMaterializationPersistenceResult,
    PairSignalMaterializationRequest,
    PairSignalSelectionOutcome,
    SignalStorageOrigin,
    SignalStoreIntegrityError,
    SourceSignalRole,
    SQLiteSignalStore,
    expected_pair_signal,
    expected_pair_signal_snapshot,
)

from tests.factories import feature, observation
from tests.pair_signal_materialization.factories import NOW, request, source_signal

MATERIALIZED_AT = NOW + timedelta(minutes=2)
ARTIFACT_TABLES = (
    "pair_signal_derivations",
    "pair_signal_derivation_observations",
    "pair_signal_materialization_completions",
)


def _append_source(
    store: SQLiteSignalStore,
    role: SourceSignalRole,
    *,
    identifier: str,
    stored_offset: int,
) -> None:
    feature_id = f"feature-{identifier}"
    store.append_observation_if_absent(observation())
    store.append_feature(feature(feature_id))
    store.append_signal(
        source_signal(
            role,
            identifier=identifier,
            feature_ids=(FeatureId(feature_id),),
        ),
        stored_at=NOW + timedelta(microseconds=stored_offset),
    )


def _claim_and_select(store: SQLiteSignalStore) -> None:
    store.claim_pair_signal_materialization(
        request(),
        captured_at=NOW + timedelta(minutes=1),
    )
    store.capture_pair_signal_selection(request())


def _selected_store(path: Path) -> SQLiteSignalStore:
    store = SQLiteSignalStore(path)
    _append_source(
        store,
        SourceSignalRole.BASE,
        identifier="signal-base",
        stored_offset=1,
    )
    _append_source(
        store,
        SourceSignalRole.QUOTE,
        identifier="signal-quote",
        stored_offset=2,
    )
    _claim_and_select(store)
    return store


def _non_selected_store(
    path: Path,
    outcome: PairSignalSelectionOutcome,
) -> SQLiteSignalStore:
    store = SQLiteSignalStore(path)
    if outcome is PairSignalSelectionOutcome.AMBIGUOUS:
        _append_source(
            store,
            SourceSignalRole.BASE,
            identifier="signal-base-a",
            stored_offset=1,
        )
        _append_source(
            store,
            SourceSignalRole.BASE,
            identifier="signal-base-b",
            stored_offset=2,
        )
        _append_source(
            store,
            SourceSignalRole.QUOTE,
            identifier="signal-quote",
            stored_offset=3,
        )
    _claim_and_select(store)
    selection = store.capture_pair_signal_selection(request()).selection_snapshot
    assert selection.outcome is outcome
    return store


def _artifact_counts(path: Path) -> tuple[int, int, int, int, int, int]:
    with sqlite3.connect(path) as connection:
        return (
            int(
                connection.execute(
                    "SELECT COUNT(*) FROM signals WHERE id LIKE 'pair-signal-%'"
                ).fetchone()[0]
            ),
            int(
                connection.execute(
                    "SELECT COUNT(*) FROM signal_sources "
                    "WHERE signal_id LIKE 'pair-signal-%'"
                ).fetchone()[0]
            ),
            int(
                connection.execute(
                    "SELECT COUNT(*) FROM signal_store_entries "
                    "WHERE storage_origin = 'PAIR_MATERIALIZATION'"
                ).fetchone()[0]
            ),
            *(int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
              for table in ARTIFACT_TABLES),
        )


def test_public_completion_contract_and_store_api_are_typed_and_immutable(
    tmp_path: Path,
) -> None:
    parameters = signature(
        SQLiteSignalStore.complete_pair_signal_materialization
    ).parameters
    assert tuple(parameters) == ("self", "request", "materialized_at")
    assert tuple(
        item.value for item in PairSignalMaterializationCompletionDisposition
    ) == ("INSERTED", "REUSED_IDENTICAL")

    result = _selected_store(tmp_path / "contract.sqlite3").complete_pair_signal_materialization(
        request(),
        materialized_at=MATERIALIZED_AT,
    )

    assert isinstance(result, PairSignalMaterializationPersistenceResult)
    assert isinstance(result.completion, PairSignalMaterializationCompletion)
    assert (
        result.completion.contract_version
        == PAIR_SIGNAL_MATERIALIZATION_COMPLETION_VERSION
    )
    with pytest.raises(FrozenInstanceError):
        result.disposition = (  # type: ignore[misc]
            PairSignalMaterializationCompletionDisposition.REUSED_IDENTICAL
        )


def test_first_selected_completion_persists_one_exact_artifact_set(
    tmp_path: Path,
) -> None:
    store = _selected_store(tmp_path / "selected.sqlite3")
    selection = store.capture_pair_signal_selection(request()).selection_snapshot

    result = store.complete_pair_signal_materialization(
        request(),
        materialized_at=MATERIALIZED_AT,
    )

    completion = result.completion
    pair_snapshot = completion.pair_signal_snapshot
    entry = completion.pair_signal_store_entry
    derivation = completion.derivation
    assert result.disposition is PairSignalMaterializationCompletionDisposition.INSERTED
    assert pair_snapshot == expected_pair_signal_snapshot(
        selection,
        materialized_at=MATERIALIZED_AT,
    )
    assert entry is not None
    assert entry.storage_origin is SignalStorageOrigin.PAIR_MATERIALIZATION
    assert entry.stored_at == MATERIALIZED_AT
    assert entry.store_sequence == 3
    assert derivation is not None
    derivation.validate_against(pair_snapshot, selection)
    assert derivation.observation_ids == pair_snapshot.source_observation_ids
    with sqlite3.connect(store.path) as connection:
        feature_ids = connection.execute(
            "SELECT feature_id FROM signal_sources WHERE signal_id = ? "
            "ORDER BY feature_id",
            (pair_snapshot.signal_id.value,),
        ).fetchall()
    assert feature_ids == [("feature-signal-base",), ("feature-signal-quote",)]
    assert _artifact_counts(store.path) == (1, 2, 1, 1, 1, 1)


@pytest.mark.parametrize(
    "outcome",
    (PairSignalSelectionOutcome.NO_MATCH, PairSignalSelectionOutcome.AMBIGUOUS),
)
def test_non_selected_completion_is_terminal_and_artifact_free(
    tmp_path: Path,
    outcome: PairSignalSelectionOutcome,
) -> None:
    store = _non_selected_store(tmp_path / f"{outcome.value}.sqlite3", outcome)

    first = store.complete_pair_signal_materialization(request())
    retried = store.complete_pair_signal_materialization(request())

    assert first.disposition is PairSignalMaterializationCompletionDisposition.INSERTED
    assert retried.disposition is (
        PairSignalMaterializationCompletionDisposition.REUSED_IDENTICAL
    )
    assert retried.completion == first.completion
    assert first.completion.outcome is outcome
    assert first.completion.pair_signal_snapshot is None
    assert first.completion.pair_signal_store_entry is None
    assert first.completion.derivation is None
    assert _artifact_counts(store.path) == (0, 0, 0, 0, 0, 1)


@pytest.mark.parametrize(
    "outcome",
    (PairSignalSelectionOutcome.NO_MATCH, PairSignalSelectionOutcome.AMBIGUOUS),
)
def test_non_selected_completion_rejects_materialized_at_before_writing(
    tmp_path: Path,
    outcome: PairSignalSelectionOutcome,
) -> None:
    store = _non_selected_store(tmp_path / f"time-{outcome.value}.sqlite3", outcome)

    with pytest.raises(ValueError, match="does not accept materialized_at"):
        store.complete_pair_signal_materialization(
            request(),
            materialized_at=MATERIALIZED_AT,
        )

    assert _artifact_counts(store.path) == (0, 0, 0, 0, 0, 0)


def test_completion_requires_persisted_request_claim_and_selection(tmp_path: Path) -> None:
    absent = SQLiteSignalStore(tmp_path / "absent.sqlite3")
    with pytest.raises(PairMaterializationPersistenceConflict, match="Request"):
        absent.complete_pair_signal_materialization(request())

    no_selection = SQLiteSignalStore(tmp_path / "no-selection.sqlite3")
    no_selection.claim_pair_signal_materialization(
        request(),
        captured_at=NOW + timedelta(minutes=1),
    )
    with pytest.raises(PairMaterializationPersistenceConflict, match="Selection"):
        no_selection.complete_pair_signal_materialization(request())

    assert _artifact_counts(absent.path) == (0, 0, 0, 0, 0, 0)
    assert _artifact_counts(no_selection.path) == (0, 0, 0, 0, 0, 0)


class _ForgedRequest(PairSignalMaterializationRequest):
    def validate_intrinsic_integrity(self) -> None:
        pass


def test_completion_rejects_request_content_different_from_persisted_request(
    tmp_path: Path,
) -> None:
    store = _selected_store(tmp_path / "forged-request.sqlite3")
    original = request()
    forged = _ForgedRequest(
        request_id=original.request_id,
        contract_version=original.contract_version,
        pair=original.pair,
        as_of=original.as_of + timedelta(seconds=1),
        specification=original.specification,
    )

    with pytest.raises(PairMaterializationPersistenceConflict, match="differs"):
        store.complete_pair_signal_materialization(
            forged,
            materialized_at=MATERIALIZED_AT,
        )

    assert _artifact_counts(store.path) == (0, 0, 0, 0, 0, 0)


@pytest.mark.parametrize(
    ("trigger", "mutation"),
    (
        (
            "pair_signal_materialization_claims_no_update",
            "UPDATE pair_signal_materialization_claims "
            "SET checkpoint_sequence = checkpoint_sequence + 10",
        ),
        (
            "pair_signal_selection_snapshots_no_update",
            "UPDATE pair_signal_selection_snapshots "
            "SET candidate_set_hash = 'candidate-set-forged'",
        ),
    ),
)
def test_completion_rejects_corrupted_claim_or_selection_before_artifact_write(
    tmp_path: Path,
    trigger: str,
    mutation: str,
) -> None:
    store = _selected_store(tmp_path / f"corrupt-source-{trigger}.sqlite3")
    with sqlite3.connect(store.path) as connection:
        connection.execute(f"DROP TRIGGER {trigger}")
        connection.execute(mutation)

    with pytest.raises(SignalStoreIntegrityError):
        store.complete_pair_signal_materialization(
            request(),
            materialized_at=MATERIALIZED_AT,
        )

    assert _artifact_counts(store.path) == (0, 0, 0, 0, 0, 0)


def test_first_selected_completion_requires_valid_utc_materialization_time(
    tmp_path: Path,
) -> None:
    store = _selected_store(tmp_path / "times.sqlite3")

    with pytest.raises(ValueError, match="requires materialized_at"):
        store.complete_pair_signal_materialization(request())
    with pytest.raises(ValueError, match="timezone-aware"):
        store.complete_pair_signal_materialization(
            request(),
            materialized_at=MATERIALIZED_AT.replace(tzinfo=None),
        )
    with pytest.raises(ValueError, match="UTC"):
        store.complete_pair_signal_materialization(
            request(),
            materialized_at=MATERIALIZED_AT.astimezone(
                timezone(timedelta(hours=9))
            ),
        )
    with pytest.raises(ValueError, match="before selection capture"):
        store.complete_pair_signal_materialization(
            request(),
            materialized_at=NOW + timedelta(seconds=30),
        )

    assert _artifact_counts(store.path) == (0, 0, 0, 0, 0, 0)


def test_selected_retry_reuses_first_materialization_time_and_store_sequence(
    tmp_path: Path,
) -> None:
    store = _selected_store(tmp_path / "retry.sqlite3")
    first = store.complete_pair_signal_materialization(
        request(),
        materialized_at=MATERIALIZED_AT,
    )

    omitted = store.complete_pair_signal_materialization(request())
    later = store.complete_pair_signal_materialization(
        request(),
        materialized_at=MATERIALIZED_AT + timedelta(days=1),
    )

    assert omitted.disposition is (
        PairSignalMaterializationCompletionDisposition.REUSED_IDENTICAL
    )
    assert later.disposition is (
        PairSignalMaterializationCompletionDisposition.REUSED_IDENTICAL
    )
    assert omitted.completion == first.completion == later.completion
    assert later.completion.pair_signal_snapshot is not None
    assert later.completion.pair_signal_snapshot.created_at == MATERIALIZED_AT
    assert later.completion.pair_signal_store_entry is not None
    assert later.completion.pair_signal_store_entry.store_sequence == 3
    assert _artifact_counts(store.path) == (1, 2, 1, 1, 1, 1)


def test_late_and_old_created_source_appends_do_not_change_completed_artifact(
    tmp_path: Path,
) -> None:
    store = _selected_store(tmp_path / "late.sqlite3")
    first = store.complete_pair_signal_materialization(
        request(),
        materialized_at=MATERIALIZED_AT,
    )
    _append_source(
        store,
        SourceSignalRole.BASE,
        identifier="signal-late",
        stored_offset=3,
    )
    store.append_feature(feature("feature-backfill"))
    backfill = source_signal(
        SourceSignalRole.BASE,
        identifier="signal-backfill",
        feature_ids=(FeatureId("feature-backfill"),),
        observed_at=NOW - timedelta(days=10),
        created_at=NOW - timedelta(days=10),
    )
    store.append_signal(backfill, stored_at=MATERIALIZED_AT + timedelta(minutes=1))

    retried = store.complete_pair_signal_materialization(request())

    assert retried.completion == first.completion
    assert _artifact_counts(store.path) == (1, 2, 1, 1, 1, 1)


def test_concurrent_selected_completion_converges_on_first_writer_time(
    tmp_path: Path,
) -> None:
    path = tmp_path / "concurrent.sqlite3"
    _selected_store(path)
    barrier = Barrier(2)
    times = (MATERIALIZED_AT, MATERIALIZED_AT + timedelta(seconds=1))

    def complete(materialized_at):
        barrier.wait()
        return SQLiteSignalStore(path).complete_pair_signal_materialization(
            request(),
            materialized_at=materialized_at,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(complete, times))

    assert {item.disposition for item in results} == {
        PairSignalMaterializationCompletionDisposition.INSERTED,
        PairSignalMaterializationCompletionDisposition.REUSED_IDENTICAL,
    }
    assert results[0].completion == results[1].completion
    assert results[0].completion.pair_signal_snapshot is not None
    assert results[0].completion.pair_signal_snapshot.created_at in times
    assert _artifact_counts(path) == (1, 2, 1, 1, 1, 1)


@pytest.mark.parametrize(
    "outcome",
    (PairSignalSelectionOutcome.NO_MATCH, PairSignalSelectionOutcome.AMBIGUOUS),
)
def test_concurrent_non_selected_completion_converges_on_one_root(
    tmp_path: Path,
    outcome: PairSignalSelectionOutcome,
) -> None:
    path = tmp_path / f"concurrent-{outcome.value}.sqlite3"
    _non_selected_store(path, outcome)
    barrier = Barrier(2)

    def complete() -> PairSignalMaterializationPersistenceResult:
        barrier.wait()
        return SQLiteSignalStore(path).complete_pair_signal_materialization(request())

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = tuple(executor.submit(complete) for _ in range(2))
        results = tuple(future.result() for future in futures)

    assert {item.disposition for item in results} == {
        PairSignalMaterializationCompletionDisposition.INSERTED,
        PairSignalMaterializationCompletionDisposition.REUSED_IDENTICAL,
    }
    assert results[0].completion == results[1].completion
    assert _artifact_counts(path) == (0, 0, 0, 0, 0, 1)


@pytest.mark.parametrize(
    "method_name",
    (
        "_append_signal",
        "_append_signal_store_entry",
        "_append_pair_signal_derivation_exact",
        "_append_materialization_completion_exact",
    ),
)
def test_each_pair_artifact_insert_stage_rolls_back_atomically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    method_name: str,
) -> None:
    store = _selected_store(tmp_path / f"rollback-{method_name}.sqlite3")

    def fail(*args, **kwargs):
        raise RuntimeError(f"test {method_name} failure")

    monkeypatch.setattr(store, method_name, fail)
    with pytest.raises(RuntimeError, match=method_name):
        store.complete_pair_signal_materialization(
            request(),
            materialized_at=MATERIALIZED_AT,
        )

    assert _artifact_counts(store.path) == (0, 0, 0, 0, 0, 0)


def test_signal_source_and_derivation_observation_failure_roll_back_all_artifacts(
    tmp_path: Path,
) -> None:
    for subject, trigger in (
        (
            "signal-source",
            "CREATE TRIGGER test_pair_source_failure BEFORE INSERT ON signal_sources "
            "WHEN NEW.signal_id LIKE 'pair-signal-%' "
            "BEGIN SELECT RAISE(ABORT, 'test pair source failure'); END",
        ),
        (
            "derivation-observation",
            "CREATE TRIGGER test_derivation_observation_failure BEFORE INSERT ON "
            "pair_signal_derivation_observations "
            "BEGIN SELECT RAISE(ABORT, 'test derivation observation failure'); END",
        ),
    ):
        store = _selected_store(tmp_path / f"rollback-{subject}.sqlite3")
        with sqlite3.connect(store.path) as connection:
            connection.execute(trigger)

        with pytest.raises(SignalStoreIntegrityError, match="constraint failed"):
            store.complete_pair_signal_materialization(
                request(),
                materialized_at=MATERIALIZED_AT,
            )

        assert _artifact_counts(store.path) == (0, 0, 0, 0, 0, 0)


def test_post_insert_hydration_failure_rolls_back_all_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _selected_store(tmp_path / "rollback-hydration.sqlite3")
    original = store._hydrate_materialization_completion
    calls = 0

    def fail_after_insert(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise SignalStoreIntegrityError("test post-insert hydration failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(store, "_hydrate_materialization_completion", fail_after_insert)
    with pytest.raises(SignalStoreIntegrityError, match="post-insert hydration"):
        store.complete_pair_signal_materialization(
            request(),
            materialized_at=MATERIALIZED_AT,
        )

    assert _artifact_counts(store.path) == (0, 0, 0, 0, 0, 0)


@pytest.mark.parametrize("with_store_entry", (False, True))
def test_existing_deterministic_pair_signal_without_completion_is_rejected(
    tmp_path: Path,
    with_store_entry: bool,
) -> None:
    store = _selected_store(tmp_path / f"orphan-{with_store_entry}.sqlite3")
    selection = store.capture_pair_signal_selection(request()).selection_snapshot
    pair_signal = expected_pair_signal(selection, materialized_at=MATERIALIZED_AT)
    with store._connect() as connection:
        store._append_signal(connection, pair_signal)
        if with_store_entry:
            store._append_signal_store_entry(
                connection,
                pair_signal.signal_id,
                MATERIALIZED_AT,
                SignalStorageOrigin.PAIR_MATERIALIZATION,
            )
        connection.commit()

    with pytest.raises(SignalStoreIntegrityError):
        store.complete_pair_signal_materialization(
            request(),
            materialized_at=MATERIALIZED_AT,
        )

    assert _artifact_counts(store.path)[-1] == 0


def test_pair_signal_and_derivation_without_completion_are_not_adopted(
    tmp_path: Path,
) -> None:
    store = _selected_store(tmp_path / "orphan-derivation.sqlite3")
    selection = store.capture_pair_signal_selection(request()).selection_snapshot
    pair_signal = expected_pair_signal(selection, materialized_at=MATERIALIZED_AT)
    snapshot = expected_pair_signal_snapshot(
        selection,
        materialized_at=MATERIALIZED_AT,
    )
    derivation = PairSignalDerivation.create(
        pair_signal_snapshot=snapshot,
        selection_snapshot=selection,
        materialized_at=MATERIALIZED_AT,
    )
    with store._connect() as connection:
        store._append_signal(connection, pair_signal)
        store._append_signal_store_entry(
            connection,
            pair_signal.signal_id,
            MATERIALIZED_AT,
            SignalStorageOrigin.PAIR_MATERIALIZATION,
        )
        store._append_pair_signal_derivation_exact(connection, derivation)
        connection.commit()

    with pytest.raises(SignalStoreIntegrityError, match="orphan|unrooted"):
        store.complete_pair_signal_materialization(
            request(),
            materialized_at=MATERIALIZED_AT,
        )

    assert _artifact_counts(store.path) == (1, 2, 1, 1, 1, 0)


def test_derivation_without_pair_signal_or_completion_is_not_adopted(
    tmp_path: Path,
) -> None:
    store = _selected_store(tmp_path / "orphan-derivation-only.sqlite3")
    selection = store.capture_pair_signal_selection(request()).selection_snapshot
    snapshot = expected_pair_signal_snapshot(
        selection,
        materialized_at=MATERIALIZED_AT,
    )
    derivation = PairSignalDerivation.create(
        pair_signal_snapshot=snapshot,
        selection_snapshot=selection,
        materialized_at=MATERIALIZED_AT,
    )
    with sqlite3.connect(store.path) as connection:
        store._append_pair_signal_derivation_exact(connection, derivation)

    with pytest.raises(SignalStoreIntegrityError, match="orphan"):
        store.complete_pair_signal_materialization(
            request(),
            materialized_at=MATERIALIZED_AT,
        )

    assert _artifact_counts(store.path) == (0, 0, 0, 1, 1, 0)


@pytest.mark.parametrize(
    ("trigger", "mutation"),
    (
        ("signals_no_update", "UPDATE signals SET direction = 0.1 WHERE id LIKE 'pair-signal-%'"),
        ("signals_no_update", "UPDATE signals SET strength = 0.1 WHERE id LIKE 'pair-signal-%'"),
        ("signals_no_update", "UPDATE signals SET confidence = 0.1 WHERE id LIKE 'pair-signal-%'"),
        (
            "signals_no_update",
            "UPDATE signals SET transformation_version = 'forged' "
            "WHERE id LIKE 'pair-signal-%'",
        ),
        (
            "signal_sources_no_delete",
            "DELETE FROM signal_sources WHERE signal_id LIKE 'pair-signal-%' "
            "AND feature_id = (SELECT MIN(feature_id) FROM signal_sources "
            "WHERE signal_id LIKE 'pair-signal-%')",
        ),
        (
            "signal_store_entries_no_update",
            "UPDATE signal_store_entries SET storage_origin = 'APPEND' "
            "WHERE storage_origin = 'PAIR_MATERIALIZATION'",
        ),
        (
            "signal_store_entries_no_update",
            "UPDATE signal_store_entries SET stored_at = '2026-07-18T00:00:00+00:00' "
            "WHERE storage_origin = 'PAIR_MATERIALIZATION'",
        ),
        (
            "pair_signal_derivations_no_update",
            "UPDATE pair_signal_derivations SET pair_signal_content_hash = 'forged'",
        ),
        (
            "pair_signal_derivations_no_update",
            "UPDATE pair_signal_derivations SET base_signal_id = quote_signal_id",
        ),
        (
            "pair_signal_derivations_no_update",
            "UPDATE pair_signal_derivations SET base_candidate_id = quote_candidate_id",
        ),
        (
            "pair_signal_derivations_no_update",
            "UPDATE pair_signal_derivations "
            "SET observation_group_identity = 'observation-group-forged'",
        ),
        (
            "pair_signal_derivations_no_update",
            "UPDATE pair_signal_derivations "
            "SET materialized_at = '2026-07-18T00:00:00+00:00'",
        ),
        (
            "pair_signal_derivation_observations_no_delete",
            "DELETE FROM pair_signal_derivation_observations",
        ),
        (
            "pair_signal_derivation_observations_no_update",
            "UPDATE pair_signal_derivation_observations SET observation_ordinal = 1",
        ),
        (
            "pair_signal_materialization_completions_no_update",
            "UPDATE pair_signal_materialization_completions "
            "SET pair_signal_store_sequence = 1",
        ),
        (
            "pair_signal_materialization_completions_no_update",
            "UPDATE pair_signal_materialization_completions "
            "SET selection_outcome = 'NO_MATCH'",
        ),
        (
            "pair_signal_materialization_completions_no_update",
            "UPDATE pair_signal_materialization_completions "
            "SET pair_signal_id = 'signal-base'",
        ),
        (
            "pair_signal_materialization_completions_no_update",
            "UPDATE pair_signal_materialization_completions "
            "SET derivation_id = 'pair-signal-derivation-forged'",
        ),
        (
            "pair_signal_materialization_completions_no_update",
            "UPDATE pair_signal_materialization_completions SET derivation_id = NULL",
        ),
    ),
)
def test_retry_rejects_corrupted_pair_artifact_without_repair(
    tmp_path: Path,
    trigger: str,
    mutation: str,
) -> None:
    store = _selected_store(tmp_path / f"corrupt-{trigger}.sqlite3")
    store.complete_pair_signal_materialization(
        request(),
        materialized_at=MATERIALIZED_AT,
    )
    with sqlite3.connect(store.path) as connection:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(f"DROP TRIGGER {trigger}")
        connection.execute(mutation)
    corrupted = _artifact_counts(store.path)

    with pytest.raises(SignalStoreIntegrityError):
        store.complete_pair_signal_materialization(request())

    assert _artifact_counts(store.path) == corrupted


def test_retry_rejects_added_pair_feature_and_derivation_observation_lineage(
    tmp_path: Path,
) -> None:
    feature_store = _selected_store(tmp_path / "added-feature.sqlite3")
    feature_result = feature_store.complete_pair_signal_materialization(
        request(),
        materialized_at=MATERIALIZED_AT,
    )
    feature_store.append_feature(feature("feature-extra"))
    assert feature_result.completion.pair_signal_snapshot is not None
    with sqlite3.connect(feature_store.path) as connection:
        connection.execute(
            "INSERT INTO signal_sources(signal_id, feature_id) VALUES (?, ?)",
            (
                feature_result.completion.pair_signal_snapshot.signal_id.value,
                "feature-extra",
            ),
        )
    with pytest.raises(SignalStoreIntegrityError):
        feature_store.complete_pair_signal_materialization(request())

    observation_store = _selected_store(tmp_path / "added-observation.sqlite3")
    observation_store.complete_pair_signal_materialization(
        request(),
        materialized_at=MATERIALIZED_AT,
    )
    observation_store.append_observation(observation("observation-extra"))
    with sqlite3.connect(observation_store.path) as connection:
        derivation_id = connection.execute(
            "SELECT derivation_id FROM pair_signal_derivations"
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO pair_signal_derivation_observations("
            "derivation_id, observation_ordinal, observation_id) VALUES (?, 1, ?)",
            (derivation_id, "observation-extra"),
        )
    with pytest.raises(SignalStoreIntegrityError):
        observation_store.complete_pair_signal_materialization(request())


def test_artifact_tables_are_immutable_and_completion_checks_cardinality(
    tmp_path: Path,
) -> None:
    store = _selected_store(tmp_path / "immutable-artifacts.sqlite3")
    store.complete_pair_signal_materialization(
        request(),
        materialized_at=MATERIALIZED_AT,
    )
    mutations = (
        ("pair_signal_derivations", "materialized_at = materialized_at"),
        (
            "pair_signal_derivation_observations",
            "observation_id = observation_id",
        ),
        (
            "pair_signal_materialization_completions",
            "selection_outcome = selection_outcome",
        ),
    )
    for table, assignment in mutations:
        with sqlite3.connect(store.path) as connection, pytest.raises(
            sqlite3.IntegrityError, match="immutable"
        ):
            connection.execute(f"UPDATE {table} SET {assignment}")
        with sqlite3.connect(store.path) as connection, pytest.raises(
            sqlite3.IntegrityError, match="immutable"
        ):
            connection.execute(f"DELETE FROM {table}")

    no_match = _non_selected_store(
        tmp_path / "invalid-non-selected.sqlite3",
        PairSignalSelectionOutcome.NO_MATCH,
    )
    selection = no_match.capture_pair_signal_selection(request()).selection_snapshot
    with sqlite3.connect(no_match.path) as connection, pytest.raises(
        sqlite3.IntegrityError
    ):
        connection.execute(
            """
            INSERT INTO pair_signal_materialization_completions(
                request_id, contract_version, selection_snapshot_id,
                selection_outcome, pair_signal_id, pair_signal_store_sequence,
                derivation_id
            ) VALUES (?, ?, ?, 'NO_MATCH', 'forged', 1, 'forged')
            """,
            (
                request().request_id,
                PAIR_SIGNAL_MATERIALIZATION_COMPLETION_VERSION,
                selection.selection_snapshot_id,
            ),
        )
    assert _artifact_counts(no_match.path) == (0, 0, 0, 0, 0, 0)


def test_completion_uses_one_connection_and_no_public_nested_store_api(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _selected_store(tmp_path / "connection.sqlite3")
    original_connect = store._connect
    connection_count = 0

    def counted_connect():
        nonlocal connection_count
        connection_count += 1
        return original_connect()

    def forbidden(*args, **kwargs):
        raise AssertionError("public Store API was called inside completion")

    monkeypatch.setattr(store, "_connect", counted_connect)
    for name in (
        "get_signal",
        "get_lineage",
        "get_signal_store_entry",
        "current_signal_checkpoint",
        "claim_pair_signal_materialization",
        "capture_pair_signal_selection",
    ):
        monkeypatch.setattr(store, name, forbidden)

    store.complete_pair_signal_materialization(
        request(),
        materialized_at=MATERIALIZED_AT,
    )

    assert connection_count == 1
