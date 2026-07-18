import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fx_core import SignalId
from fx_signal_store import (
    PAIR_SIGNAL_MATERIALIZATION_CLAIM_VERSION,
    PairMaterializationPersistenceConflict,
    PairSignalMaterializationClaim,
    PairSignalMaterializationRequest,
    PairSignalMaterializationSpecification,
    SignalStoreIntegrityError,
    SQLiteSignalStore,
)

from tests.factories import feature, observation, signal
from tests.pair_signal_materialization.factories import NOW, request, specification


def _store_with_source(path: Path) -> SQLiteSignalStore:
    store = SQLiteSignalStore(path)
    store.append_observation(observation())
    store.append_feature(feature())
    return store


def _counts(path: Path) -> tuple[int, int, int]:
    with sqlite3.connect(path) as connection:
        return tuple(
            int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in (
                "pair_signal_materialization_specifications",
                "pair_signal_materialization_requests",
                "pair_signal_materialization_claims",
            )
        )  # type: ignore[return-value]


def test_materialization_claim_contract_is_immutable_and_validated() -> None:
    claim = PairSignalMaterializationClaim(
        contract_version=PAIR_SIGNAL_MATERIALIZATION_CLAIM_VERSION,
        request=request(),
        checkpoint_sequence=0,
        captured_at=NOW + timedelta(minutes=1),
    )

    with pytest.raises(FrozenInstanceError):
        claim.checkpoint_sequence = 1  # type: ignore[misc]
    with pytest.raises(ValueError, match="non-negative integer"):
        replace(claim, checkpoint_sequence=-1)
    with pytest.raises(ValueError, match="non-negative integer"):
        replace(claim, checkpoint_sequence=True)
    with pytest.raises(ValueError, match="before request"):
        replace(claim, captured_at=NOW - timedelta(microseconds=1))
    with pytest.raises(ValueError, match="UTC"):
        replace(
            claim,
            captured_at=(NOW + timedelta(minutes=1)).astimezone(
                timezone(timedelta(hours=9))
            ),
        )
    with pytest.raises(ValueError, match="unsupported"):
        replace(claim, contract_version="pair-signal-materialization-claim-v2")


def test_first_claim_freezes_empty_store_checkpoint(tmp_path: Path) -> None:
    store = SQLiteSignalStore(tmp_path / "signals.sqlite3")
    captured_at = NOW + timedelta(minutes=1)

    claim = store.claim_pair_signal_materialization(
        request(),
        captured_at=captured_at,
    )

    assert claim.checkpoint_sequence == 0
    assert claim.captured_at == captured_at
    assert claim.request == request()


def test_first_claim_freezes_current_store_sequence(tmp_path: Path) -> None:
    store = _store_with_source(tmp_path / "signals.sqlite3")
    store.append_signal(signal(identifier="signal-a"), stored_at=NOW)
    store.append_signal(
        signal(identifier="signal-b"),
        stored_at=NOW + timedelta(microseconds=1),
    )

    claim = store.claim_pair_signal_materialization(
        request(),
        captured_at=NOW + timedelta(minutes=1),
    )

    assert claim.checkpoint_sequence == 2
    assert store.current_signal_checkpoint() == 2


def test_retry_reuses_first_checkpoint_and_captured_at(tmp_path: Path) -> None:
    store = _store_with_source(tmp_path / "signals.sqlite3")
    store.append_signal(signal(), stored_at=NOW)
    first_captured_at = NOW + timedelta(minutes=1)
    first = store.claim_pair_signal_materialization(
        request(),
        captured_at=first_captured_at,
    )
    store.append_signal(
        signal(identifier="signal-late"),
        stored_at=NOW + timedelta(minutes=2),
    )

    retried = store.claim_pair_signal_materialization(
        request(),
        captured_at=NOW + timedelta(minutes=5),
    )

    assert retried == first
    assert retried.checkpoint_sequence == 1
    assert retried.captured_at == first_captured_at
    assert store.current_signal_checkpoint() == 2


def test_old_created_backfill_after_claim_does_not_change_checkpoint(
    tmp_path: Path,
) -> None:
    store = _store_with_source(tmp_path / "signals.sqlite3")
    store.append_signal(signal(), stored_at=NOW)
    first = store.claim_pair_signal_materialization(
        request(),
        captured_at=NOW + timedelta(minutes=1),
    )
    backfill = replace(
        signal(identifier="signal-backfill"),
        observed_at=NOW - timedelta(days=30),
        created_at=NOW - timedelta(days=30),
    )
    store.append_signal(backfill, stored_at=NOW + timedelta(minutes=2))

    retried = store.claim_pair_signal_materialization(
        request(),
        captured_at=NOW + timedelta(minutes=3),
    )

    assert retried == first
    assert retried.checkpoint_sequence == 1
    assert store.get_signal_store_entry(SignalId("signal-backfill")).store_sequence == 2


def test_claim_and_exact_request_content_survive_store_reopen(tmp_path: Path) -> None:
    path = tmp_path / "signals.sqlite3"
    store = SQLiteSignalStore(path)
    expected = store.claim_pair_signal_materialization(
        request(),
        captured_at=NOW + timedelta(minutes=1),
    )

    reopened = SQLiteSignalStore(path)
    actual = reopened.claim_pair_signal_materialization(
        request(),
        captured_at=NOW + timedelta(minutes=5),
    )

    assert actual == expected
    with sqlite3.connect(path) as connection:
        specification_row = connection.execute(
            "SELECT * FROM pair_signal_materialization_specifications"
        ).fetchone()
        request_row = connection.execute(
            "SELECT * FROM pair_signal_materialization_requests"
        ).fetchone()
    assert specification_row is not None
    assert request_row is not None
    assert specification_row[0] == specification().specification_id
    assert specification_row[12] == 14_400_000_000
    assert request_row[0] == request().request_id
    assert request_row[4] == specification().specification_id


class _ForgedSpecification(PairSignalMaterializationSpecification):
    def validate_intrinsic_integrity(self) -> None:
        pass


class _ForgedRequest(PairSignalMaterializationRequest):
    def validate_intrinsic_integrity(self) -> None:
        pass


def _forged_specification() -> PairSignalMaterializationSpecification:
    original = specification()
    return _ForgedSpecification(
        specification_id=original.specification_id,
        contract_version=original.contract_version,
        pair=original.pair,
        source_signal_type=original.source_signal_type,
        output_signal_type=original.output_signal_type,
        horizon=original.horizon,
        producer_version="producer-forged",
        model_version=original.model_version,
        prompt_version=original.prompt_version,
        scorer_version=original.scorer_version,
        expected_source_transformation_version=(
            original.expected_source_transformation_version
        ),
        output_transformation_version=original.output_transformation_version,
        source_signal_max_age=original.source_signal_max_age,
        observation_group_policy_version=original.observation_group_policy_version,
        selection_policy_version=original.selection_policy_version,
    )


def _forged_request(
    *,
    materialization_specification: PairSignalMaterializationSpecification,
    as_of: datetime,
    request_id: str | None = None,
) -> PairSignalMaterializationRequest:
    original = request()
    return _ForgedRequest(
        request_id=request_id or original.request_id,
        contract_version=original.contract_version,
        pair=original.pair,
        as_of=as_of,
        specification=materialization_specification,
    )


def test_same_specification_id_with_conflicting_content_is_rejected(
    tmp_path: Path,
) -> None:
    store = SQLiteSignalStore(tmp_path / "signals.sqlite3")
    store.claim_pair_signal_materialization(
        request(),
        captured_at=NOW + timedelta(minutes=1),
    )
    forged = _forged_request(
        materialization_specification=_forged_specification(),
        as_of=NOW,
    )

    with pytest.raises(PairMaterializationPersistenceConflict, match="specification"):
        store.claim_pair_signal_materialization(
            forged,
            captured_at=NOW + timedelta(minutes=2),
        )

    assert _counts(store.path) == (1, 1, 1)


def test_same_request_id_with_conflicting_content_is_rejected(tmp_path: Path) -> None:
    store = SQLiteSignalStore(tmp_path / "signals.sqlite3")
    store.claim_pair_signal_materialization(
        request(),
        captured_at=NOW + timedelta(minutes=1),
    )
    forged = _forged_request(
        materialization_specification=specification(),
        as_of=NOW + timedelta(seconds=1),
    )

    with pytest.raises(PairMaterializationPersistenceConflict, match="request"):
        store.claim_pair_signal_materialization(
            forged,
            captured_at=NOW + timedelta(minutes=2),
        )

    assert _counts(store.path) == (1, 1, 1)


def test_same_pair_as_of_and_specification_cannot_use_another_request_id(
    tmp_path: Path,
) -> None:
    store = SQLiteSignalStore(tmp_path / "signals.sqlite3")
    store.claim_pair_signal_materialization(
        request(),
        captured_at=NOW + timedelta(minutes=1),
    )
    forged = _forged_request(
        materialization_specification=specification(),
        as_of=NOW,
        request_id="pair-signal-request-forged-business-key",
    )

    with pytest.raises(PairMaterializationPersistenceConflict, match="another request"):
        store.claim_pair_signal_materialization(
            forged,
            captured_at=NOW + timedelta(minutes=2),
        )

    assert _counts(store.path) == (1, 1, 1)


def test_invalid_claim_time_leaves_no_specification_request_or_claim(
    tmp_path: Path,
) -> None:
    store = SQLiteSignalStore(tmp_path / "signals.sqlite3")
    non_utc = (NOW + timedelta(minutes=1)).astimezone(
        timezone(timedelta(hours=9))
    )

    with pytest.raises(ValueError, match="UTC"):
        store.claim_pair_signal_materialization(request(), captured_at=non_utc)

    assert _counts(store.path) == (0, 0, 0)


def test_claim_failure_rolls_back_specification_request_and_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = SQLiteSignalStore(tmp_path / "signals.sqlite3")

    def fail_checkpoint(connection: sqlite3.Connection) -> int:
        raise RuntimeError("checkpoint failure")

    monkeypatch.setattr(store, "_current_signal_checkpoint", fail_checkpoint)
    with pytest.raises(RuntimeError, match="checkpoint failure"):
        store.claim_pair_signal_materialization(
            request(),
            captured_at=NOW + timedelta(minutes=1),
        )

    assert _counts(store.path) == (0, 0, 0)


def test_missing_store_entry_rejects_claim_without_partial_rows(tmp_path: Path) -> None:
    store = _store_with_source(tmp_path / "signals.sqlite3")
    store.append_signal(signal(), stored_at=NOW)
    with sqlite3.connect(store.path) as connection:
        connection.execute("DROP TRIGGER signal_store_entries_no_delete")
        connection.execute("DELETE FROM signal_store_entries WHERE signal_id = 'signal-1'")

    with pytest.raises(SignalStoreIntegrityError, match="without a Store entry"):
        store.claim_pair_signal_materialization(
            request(),
            captured_at=NOW + timedelta(minutes=1),
        )

    assert _counts(store.path) == (0, 0, 0)


def test_orphan_store_entry_rejects_claim_without_partial_rows(tmp_path: Path) -> None:
    store = SQLiteSignalStore(tmp_path / "signals.sqlite3")
    with sqlite3.connect(store.path) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute(
            """
            INSERT INTO signal_store_entries(
                contract_version, signal_id, stored_at, storage_origin
            ) VALUES ('signal-store-entry-v1', 'signal-orphan', ?, 'APPEND')
            """,
            (NOW.isoformat(),),
        )

    with pytest.raises(SignalStoreIntegrityError, match="without a Signal"):
        store.claim_pair_signal_materialization(
            request(),
            captured_at=NOW + timedelta(minutes=1),
        )

    assert _counts(store.path) == (0, 0, 0)


def test_retry_rejects_claim_checkpoint_above_current_catalog(
    tmp_path: Path,
) -> None:
    store = _store_with_source(tmp_path / "signals.sqlite3")
    store.append_signal(signal(), stored_at=NOW)
    original = store.claim_pair_signal_materialization(
        request(),
        captured_at=NOW + timedelta(minutes=1),
    )
    with sqlite3.connect(store.path) as connection:
        connection.execute("DROP TRIGGER pair_signal_materialization_claims_no_update")
        connection.execute(
            "UPDATE pair_signal_materialization_claims SET checkpoint_sequence = 2"
        )

    with pytest.raises(SignalStoreIntegrityError, match="exceeds current checkpoint"):
        store.claim_pair_signal_materialization(
            request(),
            captured_at=NOW + timedelta(minutes=5),
        )

    with sqlite3.connect(store.path) as connection:
        row = connection.execute(
            "SELECT checkpoint_sequence, captured_at "
            "FROM pair_signal_materialization_claims"
        ).fetchone()
    assert row == (2, original.captured_at.isoformat())


def test_retry_rejects_claim_checkpoint_without_exact_store_boundary(
    tmp_path: Path,
) -> None:
    store = _store_with_source(tmp_path / "signals.sqlite3")
    store.append_signal(signal(identifier="signal-a"), stored_at=NOW)
    original = store.claim_pair_signal_materialization(
        request(),
        captured_at=NOW + timedelta(minutes=1),
    )
    store.append_signal(
        signal(identifier="signal-b"),
        stored_at=NOW + timedelta(minutes=2),
    )
    with sqlite3.connect(store.path) as connection:
        connection.execute("DROP TRIGGER signal_store_entries_no_update")
        connection.execute(
            "UPDATE signal_store_entries SET store_sequence = 3 "
            "WHERE signal_id = 'signal-b'"
        )
        connection.execute("DROP TRIGGER pair_signal_materialization_claims_no_update")
        connection.execute(
            "UPDATE pair_signal_materialization_claims SET checkpoint_sequence = 2"
        )

    with pytest.raises(SignalStoreIntegrityError, match="does not reference a Store entry"):
        store.claim_pair_signal_materialization(
            request(),
            captured_at=NOW + timedelta(minutes=5),
        )

    with sqlite3.connect(store.path) as connection:
        row = connection.execute(
            "SELECT checkpoint_sequence, captured_at "
            "FROM pair_signal_materialization_claims"
        ).fetchone()
    assert row == (2, original.captured_at.isoformat())


def test_two_store_instances_converge_on_one_first_written_claim(
    tmp_path: Path,
) -> None:
    path = tmp_path / "signals.sqlite3"
    first_store = SQLiteSignalStore(path)
    second_store = SQLiteSignalStore(path)
    captured_times = (
        NOW + timedelta(minutes=1),
        NOW + timedelta(minutes=2),
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = tuple(
            executor.submit(
                store.claim_pair_signal_materialization,
                request(),
                captured_at=captured_at,
            )
            for store, captured_at in zip(
                (first_store, second_store), captured_times, strict=True
            )
        )
        claims = tuple(future.result() for future in futures)

    assert claims[0] == claims[1]
    assert claims[0].captured_at in captured_times
    assert _counts(path) == (1, 1, 1)


class _ClaimConnectionCountingStore(SQLiteSignalStore):
    connection_count: int = 0

    def _connect(self) -> sqlite3.Connection:
        self.connection_count += 1
        return super()._connect()

    def get_signal(self, signal_id: SignalId):
        raise AssertionError("claim must not call public get_signal")

    def get_lineage(self, signal_id: SignalId):
        raise AssertionError("claim must not call public get_lineage")

    def list_signals(self, **kwargs):
        raise AssertionError("claim must not call public list_signals")

    def current_signal_checkpoint(self) -> int:
        raise AssertionError("claim must use its transaction connection")


def test_claim_uses_one_connection_and_no_nested_public_store_api(tmp_path: Path) -> None:
    store = _ClaimConnectionCountingStore(tmp_path / "signals.sqlite3")
    store.connection_count = 0

    claim = store.claim_pair_signal_materialization(
        request(),
        captured_at=NOW + timedelta(minutes=1),
    )

    assert claim.checkpoint_sequence == 0
    assert store.connection_count == 1
