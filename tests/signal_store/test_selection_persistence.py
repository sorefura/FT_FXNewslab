import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
from datetime import timedelta
from inspect import signature
from pathlib import Path

import pytest
from fx_core import FeatureId, ObservationId, SignalId
from fx_signal_store import (
    PairMaterializationPersistenceConflict,
    PairSignalCandidateEligibility,
    PairSignalCandidateRejectionReason,
    PairSignalMaterializationRequest,
    PairSignalSelectionOutcome,
    PairSignalSelectionPersistenceDisposition,
    PairSignalSelectionPersistenceResult,
    PairSignalSelectionReason,
    SignalStoreIntegrityError,
    SourceSignalRole,
    SQLiteSignalStore,
)

from tests.factories import feature, observation
from tests.pair_signal_materialization.factories import NOW, request, source_signal


def _append_source(
    store: SQLiteSignalStore,
    role: SourceSignalRole,
    *,
    identifier: str,
    observation_id: str = "observation-1",
    stored_offset: int = 0,
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
        stored_at=NOW + timedelta(microseconds=stored_offset),
    )


def _claim(store: SQLiteSignalStore) -> None:
    store.claim_pair_signal_materialization(
        request(),
        captured_at=NOW + timedelta(minutes=1),
    )


def _selection_counts(path: Path) -> tuple[int, int, int]:
    with sqlite3.connect(path) as connection:
        return tuple(
            int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in (
                "pair_signal_selection_snapshots",
                "pair_signal_selection_candidates",
                "pair_signal_selection_candidate_observations",
            )
        )  # type: ignore[return-value]


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
    _claim(store)
    return store


def test_public_selection_persistence_result_is_typed_and_immutable(
    tmp_path: Path,
) -> None:
    parameters = signature(SQLiteSignalStore.capture_pair_signal_selection).parameters
    assert tuple(parameters) == ("self", "request")
    assert tuple(item.value for item in PairSignalSelectionPersistenceDisposition) == (
        "INSERTED",
        "REUSED_IDENTICAL",
    )

    store = SQLiteSignalStore(tmp_path / "contract-selection.sqlite3")
    _claim(store)
    result = store.capture_pair_signal_selection(request())

    assert isinstance(result, PairSignalSelectionPersistenceResult)
    with pytest.raises(FrozenInstanceError):
        result.disposition = (  # type: ignore[misc]
            PairSignalSelectionPersistenceDisposition.REUSED_IDENTICAL
        )


def test_empty_checkpoint_persists_terminal_no_match_and_empty_inventory(
    tmp_path: Path,
) -> None:
    store = SQLiteSignalStore(tmp_path / "signals.sqlite3")
    _claim(store)

    result = store.capture_pair_signal_selection(request())

    assert result.disposition is PairSignalSelectionPersistenceDisposition.INSERTED
    assert result.selection_snapshot.candidates == ()
    assert result.selection_snapshot.outcome is PairSignalSelectionOutcome.NO_MATCH
    assert result.selection_snapshot.reason is (
        PairSignalSelectionReason.NO_ELIGIBLE_BASE_SIGNAL
    )
    assert _selection_counts(store.path) == (1, 0, 0)


def test_every_checkpoint_signal_creates_base_and_quote_evidence_in_canonical_order(
    tmp_path: Path,
) -> None:
    store = _selected_store(tmp_path / "signals.sqlite3")

    snapshot = store.capture_pair_signal_selection(request()).selection_snapshot

    assert len(snapshot.candidates) == 4
    assert tuple(
        (item.role, item.signal_snapshot.signal_id.value)
        for item in snapshot.candidates
    ) == (
        (SourceSignalRole.BASE, "signal-base"),
        (SourceSignalRole.BASE, "signal-quote"),
        (SourceSignalRole.QUOTE, "signal-base"),
        (SourceSignalRole.QUOTE, "signal-quote"),
    )
    assert snapshot.outcome is PairSignalSelectionOutcome.SELECTED
    assert snapshot.selected_base_signal_id == SignalId("signal-base")
    assert snapshot.selected_quote_signal_id == SignalId("signal-quote")
    assert _selection_counts(store.path) == (1, 4, 4)
    with sqlite3.connect(store.path) as connection:
        candidate_ordinals = connection.execute(
            "SELECT candidate_ordinal FROM pair_signal_selection_candidates "
            "ORDER BY candidate_ordinal"
        ).fetchall()
        observation_ordinals = connection.execute(
            "SELECT observation_ordinal FROM "
            "pair_signal_selection_candidate_observations "
            "ORDER BY candidate_id, observation_ordinal"
        ).fetchall()
    assert candidate_ordinals == [(0,), (1,), (2,), (3,)]
    assert observation_ordinals == [(0,), (0,), (0,), (0,)]


def test_candidate_order_is_canonical_when_signal_store_insertion_order_changes(
    tmp_path: Path,
) -> None:
    first = SQLiteSignalStore(tmp_path / "first.sqlite3")
    _append_source(first, SourceSignalRole.BASE, identifier="signal-z", stored_offset=1)
    _append_source(
        first,
        SourceSignalRole.QUOTE,
        identifier="signal-a",
        stored_offset=2,
    )
    _claim(first)
    second = SQLiteSignalStore(tmp_path / "second.sqlite3")
    _append_source(
        second,
        SourceSignalRole.QUOTE,
        identifier="signal-a",
        stored_offset=1,
    )
    _append_source(second, SourceSignalRole.BASE, identifier="signal-z", stored_offset=2)
    _claim(second)

    first_snapshot = first.capture_pair_signal_selection(request()).selection_snapshot
    second_snapshot = second.capture_pair_signal_selection(request()).selection_snapshot

    def canonical_subjects(snapshot):
        return tuple(
            (item.role, item.signal_snapshot.signal_id)
            for item in snapshot.candidates
        )
    assert canonical_subjects(first_snapshot) == canonical_subjects(second_snapshot)
    assert canonical_subjects(first_snapshot) == (
        (SourceSignalRole.BASE, SignalId("signal-a")),
        (SourceSignalRole.BASE, SignalId("signal-z")),
        (SourceSignalRole.QUOTE, SignalId("signal-a")),
        (SourceSignalRole.QUOTE, SignalId("signal-z")),
    )


def test_candidate_observation_lineage_uses_canonical_contiguous_ordinals(
    tmp_path: Path,
) -> None:
    store = SQLiteSignalStore(tmp_path / "signals.sqlite3")
    store.append_observation(observation("observation-z"))
    store.append_observation(observation("observation-a"))
    item_feature = replace(
        feature("feature-signal-base", "observation-z"),
        observation_ids=(
            ObservationId("observation-z"),
            ObservationId("observation-a"),
        ),
    )
    store.append_feature(item_feature)
    store.append_signal(
        source_signal(
            SourceSignalRole.BASE,
            identifier="signal-base",
            feature_ids=(item_feature.feature_id,),
        ),
        stored_at=NOW,
    )
    _claim(store)

    snapshot = store.capture_pair_signal_selection(request()).selection_snapshot

    assert all(
        item.observation_ids
        == (ObservationId("observation-a"), ObservationId("observation-z"))
        for item in snapshot.candidates
    )
    with sqlite3.connect(store.path) as connection:
        rows = connection.execute(
            "SELECT candidate_id, observation_ordinal, observation_id "
            "FROM pair_signal_selection_candidate_observations "
            "ORDER BY candidate_id, observation_ordinal"
        ).fetchall()
    grouped = {
        candidate_id: tuple(
            (ordinal, observation_id)
            for row_candidate_id, ordinal, observation_id in rows
            if row_candidate_id == candidate_id
        )
        for candidate_id, _, _ in rows
    }
    assert all(
        values == ((0, "observation-a"), (1, "observation-z"))
        for values in grouped.values()
    )


def test_ineligible_role_and_version_candidates_remain_persisted_and_rederived(
    tmp_path: Path,
) -> None:
    store = SQLiteSignalStore(tmp_path / "signals.sqlite3")
    _append_source(
        store,
        SourceSignalRole.BASE,
        identifier="signal-base",
        scorer_version="scorer-other",
    )
    _claim(store)

    snapshot = store.capture_pair_signal_selection(request()).selection_snapshot

    assert len(snapshot.candidates) == 2
    assert all(
        item.eligibility is PairSignalCandidateEligibility.INELIGIBLE
        for item in snapshot.candidates
    )
    assert {
        item.rejection_reason for item in snapshot.candidates
    } == {
        PairSignalCandidateRejectionReason.SCORER_VERSION_MISMATCH,
        PairSignalCandidateRejectionReason.TARGET_CURRENCY_MISMATCH,
    }
    assert SQLiteSignalStore(store.path).capture_pair_signal_selection(
        request()
    ).selection_snapshot == snapshot


@pytest.mark.parametrize(
    ("signal_changes", "reason"),
    [
        (
            {
                "observed_at": NOW + timedelta(seconds=1),
                "created_at": NOW + timedelta(seconds=1),
            },
            PairSignalCandidateRejectionReason.OBSERVED_AFTER_AS_OF,
        ),
        (
            {
                "observed_at": NOW - timedelta(hours=5),
                "created_at": NOW - timedelta(hours=4, minutes=59),
            },
            PairSignalCandidateRejectionReason.STALE_AT_AS_OF,
        ),
    ],
)
def test_temporal_rejection_reason_survives_persistence_reconstruction(
    tmp_path: Path,
    signal_changes: dict[str, object],
    reason: PairSignalCandidateRejectionReason,
) -> None:
    store = SQLiteSignalStore(tmp_path / "signals.sqlite3")
    _append_source(
        store,
        SourceSignalRole.BASE,
        identifier="signal-temporal",
        **signal_changes,
    )
    _claim(store)

    first = store.capture_pair_signal_selection(request())
    retried = store.capture_pair_signal_selection(request())

    base = next(
        item
        for item in first.selection_snapshot.candidates
        if item.role is SourceSignalRole.BASE
    )
    assert base.rejection_reason is reason
    assert retried.selection_snapshot == first.selection_snapshot


def test_claim_checkpoint_excludes_late_old_created_signal_on_first_and_retry(
    tmp_path: Path,
) -> None:
    store = SQLiteSignalStore(tmp_path / "signals.sqlite3")
    _append_source(store, SourceSignalRole.BASE, identifier="signal-before")
    _claim(store)
    _append_source(
        store,
        SourceSignalRole.QUOTE,
        identifier="signal-backfill-after",
        stored_offset=2,
        observed_at=NOW - timedelta(days=30),
        created_at=NOW - timedelta(days=30),
    )

    first = store.capture_pair_signal_selection(request())
    retried = store.capture_pair_signal_selection(request())

    assert {
        item.signal_snapshot.signal_id.value
        for item in first.selection_snapshot.candidates
    } == {"signal-before"}
    assert len(first.selection_snapshot.candidates) == 2
    assert first.selection_snapshot.reason is (
        PairSignalSelectionReason.NO_ELIGIBLE_QUOTE_SIGNAL
    )
    assert retried.disposition is (
        PairSignalSelectionPersistenceDisposition.REUSED_IDENTICAL
    )
    assert retried.selection_snapshot == first.selection_snapshot


def test_complete_groups_persist_no_match_and_ambiguity_without_trimming_inventory(
    tmp_path: Path,
) -> None:
    no_group = SQLiteSignalStore(tmp_path / "no-group.sqlite3")
    _append_source(
        no_group,
        SourceSignalRole.BASE,
        identifier="signal-base",
        observation_id="observation-base",
    )
    _append_source(
        no_group,
        SourceSignalRole.QUOTE,
        identifier="signal-quote",
        observation_id="observation-quote",
        stored_offset=1,
    )
    _claim(no_group)
    no_group_snapshot = no_group.capture_pair_signal_selection(
        request()
    ).selection_snapshot

    ambiguous = SQLiteSignalStore(tmp_path / "ambiguous.sqlite3")
    _append_source(ambiguous, SourceSignalRole.BASE, identifier="signal-base-a")
    _append_source(
        ambiguous,
        SourceSignalRole.BASE,
        identifier="signal-base-b",
        stored_offset=1,
    )
    _append_source(
        ambiguous,
        SourceSignalRole.QUOTE,
        identifier="signal-quote",
        stored_offset=2,
    )
    _claim(ambiguous)
    ambiguous_snapshot = ambiguous.capture_pair_signal_selection(
        request()
    ).selection_snapshot

    assert no_group_snapshot.reason is (
        PairSignalSelectionReason.NO_COMPLETE_OBSERVATION_GROUP
    )
    assert len(no_group_snapshot.candidates) == 4
    assert ambiguous_snapshot.outcome is PairSignalSelectionOutcome.AMBIGUOUS
    assert ambiguous_snapshot.reason is (
        PairSignalSelectionReason.AMBIGUOUS_BASE_SIGNAL
    )
    assert len(ambiguous_snapshot.candidates) == 6


def test_quote_ambiguity_and_semantic_top_tie_persist_exact_terminal_reasons(
    tmp_path: Path,
) -> None:
    quote_ambiguous = SQLiteSignalStore(tmp_path / "quote-ambiguous.sqlite3")
    _append_source(
        quote_ambiguous,
        SourceSignalRole.BASE,
        identifier="signal-base",
    )
    _append_source(
        quote_ambiguous,
        SourceSignalRole.QUOTE,
        identifier="signal-quote-a",
        stored_offset=1,
    )
    _append_source(
        quote_ambiguous,
        SourceSignalRole.QUOTE,
        identifier="signal-quote-b",
        stored_offset=2,
    )
    _claim(quote_ambiguous)
    quote_snapshot = quote_ambiguous.capture_pair_signal_selection(
        request()
    ).selection_snapshot

    tied = SQLiteSignalStore(tmp_path / "tied.sqlite3")
    for index, (role, group) in enumerate(
        (
            (SourceSignalRole.BASE, "observation-a"),
            (SourceSignalRole.QUOTE, "observation-a"),
            (SourceSignalRole.BASE, "observation-b"),
            (SourceSignalRole.QUOTE, "observation-b"),
        ),
        start=1,
    ):
        _append_source(
            tied,
            role,
            identifier=f"signal-{index}",
            observation_id=group,
            stored_offset=index,
        )
    _claim(tied)
    tied_snapshot = tied.capture_pair_signal_selection(request()).selection_snapshot

    assert quote_snapshot.reason is (
        PairSignalSelectionReason.AMBIGUOUS_QUOTE_SIGNAL
    )
    assert tied_snapshot.reason is (
        PairSignalSelectionReason.AMBIGUOUS_SOURCE_GROUP
    )
    assert len(tied_snapshot.candidates) == 8


def test_exact_retry_reconstructs_source_inventory_and_reuses_one_snapshot(
    tmp_path: Path,
) -> None:
    store = _selected_store(tmp_path / "signals.sqlite3")

    first = store.capture_pair_signal_selection(request())
    second = SQLiteSignalStore(store.path).capture_pair_signal_selection(request())

    assert first.disposition is PairSignalSelectionPersistenceDisposition.INSERTED
    assert second.disposition is (
        PairSignalSelectionPersistenceDisposition.REUSED_IDENTICAL
    )
    assert second.selection_snapshot == first.selection_snapshot
    assert _selection_counts(store.path) == (1, 4, 4)


def test_capture_requires_an_exact_persisted_claim_and_never_auto_claims(
    tmp_path: Path,
) -> None:
    store = SQLiteSignalStore(tmp_path / "signals.sqlite3")

    with pytest.raises(PairMaterializationPersistenceConflict, match="persisted"):
        store.capture_pair_signal_selection(request())

    with sqlite3.connect(store.path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM pair_signal_materialization_claims"
        ).fetchone() == (0,)
    assert _selection_counts(store.path) == (0, 0, 0)


class _ForgedRequest(PairSignalMaterializationRequest):
    def validate_intrinsic_integrity(self) -> None:
        pass


def test_capture_rejects_supplied_request_that_differs_from_persisted_content(
    tmp_path: Path,
) -> None:
    store = SQLiteSignalStore(tmp_path / "signals.sqlite3")
    _claim(store)
    original = request()
    forged = _ForgedRequest(
        request_id=original.request_id,
        contract_version=original.contract_version,
        pair=original.pair,
        as_of=original.as_of + timedelta(seconds=1),
        specification=original.specification,
    )

    with pytest.raises(PairMaterializationPersistenceConflict, match="differs"):
        store.capture_pair_signal_selection(forged)

    assert _selection_counts(store.path) == (0, 0, 0)


@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE pair_signal_selection_candidates "
        "SET signal_content_hash = 'signal-content-forged' "
        "WHERE candidate_ordinal = 0",
        "UPDATE pair_signal_selection_candidates "
        "SET eligibility = 'ELIGIBLE', rejection_reason = NULL "
        "WHERE candidate_ordinal = 1",
        "UPDATE pair_signal_selection_candidates "
        "SET store_sequence = store_sequence + 10 "
        "WHERE candidate_ordinal = 0",
        "UPDATE pair_signal_selection_candidates "
        "SET signal_id = 'signal-quote' WHERE candidate_ordinal = 0",
        "UPDATE pair_signal_selection_candidates "
        "SET rejection_reason = 'TARGET_TYPE_MISMATCH' "
        "WHERE candidate_ordinal = 1",
        "UPDATE pair_signal_selection_candidates "
        "SET candidate_ordinal = candidate_ordinal + 10 "
        "WHERE candidate_ordinal = 3",
        "UPDATE pair_signal_selection_snapshots "
        "SET candidate_set_hash = 'candidate-set-forged'",
        "UPDATE pair_signal_selection_snapshots "
        "SET reason = 'AMBIGUOUS_BASE_SIGNAL'",
        "UPDATE pair_signal_selection_snapshots "
        "SET outcome = 'AMBIGUOUS', reason = 'AMBIGUOUS_SOURCE_GROUP', "
        "selected_base_candidate_id = NULL, selected_quote_candidate_id = NULL, "
        "selected_base_signal_id = NULL, selected_quote_signal_id = NULL, "
        "selected_observation_group_identity = NULL",
        "UPDATE pair_signal_selection_snapshots "
        "SET selected_base_candidate_id = selected_quote_candidate_id",
        "UPDATE pair_signal_selection_snapshots "
        "SET selected_base_signal_id = selected_quote_signal_id",
        "UPDATE pair_signal_selection_snapshots "
        "SET selected_observation_group_identity = 'observation-group-forged'",
        "UPDATE pair_signal_selection_snapshots "
        "SET checkpoint_sequence = checkpoint_sequence + 1",
        "UPDATE pair_signal_selection_snapshots "
        "SET captured_at = '2026-07-17T06:05:00+00:00'",
    ],
)
def test_retry_rejects_corrupted_candidate_or_snapshot_evidence(
    tmp_path: Path,
    mutation: str,
) -> None:
    store = _selected_store(tmp_path / "signals.sqlite3")
    store.capture_pair_signal_selection(request())
    with sqlite3.connect(store.path) as connection:
        connection.execute("DROP TRIGGER pair_signal_selection_candidates_no_update")
        connection.execute("DROP TRIGGER pair_signal_selection_snapshots_no_update")
        connection.execute(mutation)

    with pytest.raises(SignalStoreIntegrityError):
        store.capture_pair_signal_selection(request())

    assert _selection_counts(store.path) == (1, 4, 4)


def test_retry_rejects_missing_candidate_observation_and_source_signal_corruption(
    tmp_path: Path,
) -> None:
    missing_path = tmp_path / "missing-observation.sqlite3"
    missing = _selected_store(missing_path)
    missing.capture_pair_signal_selection(request())
    with sqlite3.connect(missing.path) as connection:
        connection.execute(
            "DROP TRIGGER pair_signal_selection_candidate_observations_no_delete"
        )
        candidate_id = connection.execute(
            "SELECT candidate_id FROM pair_signal_selection_candidates "
            "ORDER BY candidate_ordinal LIMIT 1"
        ).fetchone()[0]
        connection.execute(
            "DELETE FROM pair_signal_selection_candidate_observations "
            "WHERE candidate_id = ?",
            (candidate_id,),
        )
    with pytest.raises(SignalStoreIntegrityError, match="candidate"):
        missing.capture_pair_signal_selection(request())

    source_path = tmp_path / "source-corrupt.sqlite3"
    source = _selected_store(source_path)
    source.capture_pair_signal_selection(request())
    with sqlite3.connect(source.path) as connection:
        connection.execute("DROP TRIGGER signals_no_update")
        connection.execute(
            "UPDATE signals SET direction = 0.1 WHERE id = 'signal-base'"
        )
    with pytest.raises(SignalStoreIntegrityError, match="differs"):
        source.capture_pair_signal_selection(request())

    lineage_path = tmp_path / "source-lineage-corrupt.sqlite3"
    lineage = _selected_store(lineage_path)
    lineage.capture_pair_signal_selection(request())
    with sqlite3.connect(lineage.path) as connection:
        connection.execute("DROP TRIGGER observations_no_delete")
        connection.execute("DELETE FROM observations WHERE id = 'observation-1'")
    with pytest.raises(SignalStoreIntegrityError, match="absent Observation"):
        lineage.capture_pair_signal_selection(request())


def test_retry_rejects_added_or_reordered_candidate_observation_evidence(
    tmp_path: Path,
) -> None:
    added = _selected_store(tmp_path / "added.sqlite3")
    added.capture_pair_signal_selection(request())
    added.append_observation(observation("observation-extra"))
    with sqlite3.connect(added.path) as connection:
        candidate_id = connection.execute(
            "SELECT candidate_id FROM pair_signal_selection_candidates "
            "ORDER BY candidate_ordinal LIMIT 1"
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO pair_signal_selection_candidate_observations "
            "VALUES (?, 1, 'observation-extra')",
            (candidate_id,),
        )
    with pytest.raises(SignalStoreIntegrityError, match="candidate"):
        added.capture_pair_signal_selection(request())

    reordered = _selected_store(tmp_path / "reordered.sqlite3")
    reordered.capture_pair_signal_selection(request())
    with sqlite3.connect(reordered.path) as connection:
        connection.execute(
            "DROP TRIGGER pair_signal_selection_candidate_observations_no_update"
        )
        connection.execute(
            "UPDATE pair_signal_selection_candidate_observations "
            "SET observation_ordinal = 2 WHERE observation_ordinal = 0"
        )
    with pytest.raises(SignalStoreIntegrityError, match="ordinals"):
        reordered.capture_pair_signal_selection(request())


def test_retry_rejects_missing_or_added_candidate_rows(
    tmp_path: Path,
) -> None:
    missing = _selected_store(tmp_path / "missing.sqlite3")
    missing.capture_pair_signal_selection(request())
    with sqlite3.connect(missing.path) as connection:
        connection.execute("DROP TRIGGER pair_signal_selection_candidates_no_delete")
        connection.execute(
            "DELETE FROM pair_signal_selection_candidates "
            "WHERE candidate_ordinal = 3"
        )
    with pytest.raises(SignalStoreIntegrityError):
        missing.capture_pair_signal_selection(request())

    added = _selected_store(tmp_path / "added.sqlite3")
    added.capture_pair_signal_selection(request())
    _append_source(
        added,
        SourceSignalRole.BASE,
        identifier="signal-late",
        stored_offset=3,
    )
    with sqlite3.connect(added.path) as connection:
        snapshot_id = connection.execute(
            "SELECT selection_snapshot_id FROM pair_signal_selection_snapshots"
        ).fetchone()[0]
        connection.execute(
            """
            INSERT INTO pair_signal_selection_candidates(
                candidate_id, selection_snapshot_id, candidate_ordinal,
                contract_version, request_id, role, signal_id,
                signal_content_hash, store_sequence, observation_group_identity,
                eligibility, rejection_reason
            ) VALUES (
                'candidate-forged-extra', ?, 4,
                'pair-signal-selection-candidate-v1', ?, 'BASE', 'signal-late',
                'signal-content-forged', 3, 'observation-group-forged',
                'ELIGIBLE', NULL
            )
            """,
            (snapshot_id, request().request_id),
        )
    with pytest.raises(SignalStoreIntegrityError):
        added.capture_pair_signal_selection(request())


@pytest.mark.parametrize(
    ("table", "when_clause"),
    [
        ("pair_signal_selection_candidates", "NEW.candidate_ordinal = 1"),
        (
            "pair_signal_selection_candidate_observations",
            "NEW.observation_ordinal = 0",
        ),
    ],
)
def test_selection_child_insert_failure_rolls_back_snapshot_and_all_evidence(
    tmp_path: Path,
    table: str,
    when_clause: str,
) -> None:
    store = _selected_store(tmp_path / "signals.sqlite3")
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            f"""
            CREATE TRIGGER reject_selection_test_insert
            BEFORE INSERT ON {table}
            WHEN {when_clause}
            BEGIN SELECT RAISE(ABORT, 'test selection child failure'); END
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="selection child failure"):
        store.capture_pair_signal_selection(request())

    assert _selection_counts(store.path) == (0, 0, 0)


def test_selection_snapshot_insert_failure_leaves_no_partial_evidence(
    tmp_path: Path,
) -> None:
    store = _selected_store(tmp_path / "signals.sqlite3")
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            """
            CREATE TRIGGER reject_selection_snapshot_test_insert
            BEFORE INSERT ON pair_signal_selection_snapshots
            BEGIN SELECT RAISE(ABORT, 'test selection Snapshot failure'); END
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="selection Snapshot failure"):
        store.capture_pair_signal_selection(request())

    assert _selection_counts(store.path) == (0, 0, 0)


def test_post_insert_hydration_failure_rolls_back_every_selection_row(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _selected_store(tmp_path / "signals.sqlite3")
    original = store._get_selection_snapshot
    call_count = 0

    def fail_after_insert(*args: object, **kwargs: object):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise SignalStoreIntegrityError("test hydration failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(store, "_get_selection_snapshot", fail_after_insert)

    with pytest.raises(SignalStoreIntegrityError, match="hydration failure"):
        store.capture_pair_signal_selection(request())

    assert _selection_counts(store.path) == (0, 0, 0)


def test_two_store_instances_converge_on_one_exact_selection_capture(
    tmp_path: Path,
) -> None:
    path = tmp_path / "signals.sqlite3"
    first_store = _selected_store(path)
    second_store = SQLiteSignalStore(path)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = tuple(
            executor.submit(store.capture_pair_signal_selection, request())
            for store in (first_store, second_store)
        )
        results = tuple(future.result() for future in futures)

    assert {item.disposition for item in results} == {
        PairSignalSelectionPersistenceDisposition.INSERTED,
        PairSignalSelectionPersistenceDisposition.REUSED_IDENTICAL,
    }
    assert results[0].selection_snapshot == results[1].selection_snapshot
    assert _selection_counts(path) == (1, 4, 4)


class _CaptureConnectionCountingStore(SQLiteSignalStore):
    connection_count: int = 0

    def _connect(self) -> sqlite3.Connection:
        self.connection_count += 1
        return super()._connect()

    def get_signal(self, signal_id: SignalId):
        raise AssertionError("capture must not call public get_signal")

    def get_lineage(self, signal_id: SignalId):
        raise AssertionError("capture must not call public get_lineage")

    def get_signal_store_entry(self, signal_id: SignalId):
        raise AssertionError("capture must not call public get_signal_store_entry")

    def current_signal_checkpoint(self) -> int:
        raise AssertionError("capture must not call public current_signal_checkpoint")

    def claim_pair_signal_materialization(self, *args: object, **kwargs: object):
        raise AssertionError("capture must not call public claim API")


def test_capture_uses_one_connection_and_only_connection_scoped_store_helpers(
    tmp_path: Path,
) -> None:
    path = tmp_path / "signals.sqlite3"
    setup = _selected_store(path)
    assert setup.current_signal_checkpoint() == 2
    store = _CaptureConnectionCountingStore(path)
    store.connection_count = 0

    result = store.capture_pair_signal_selection(request())

    assert result.selection_snapshot.outcome is PairSignalSelectionOutcome.SELECTED
    assert store.connection_count == 1
