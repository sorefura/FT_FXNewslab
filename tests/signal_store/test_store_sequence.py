import sqlite3
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from fx_core import DirectionScore, SignalId
from fx_signal_store import (
    SIGNAL_STORE_ENTRY_VERSION,
    SignalStorageOrigin,
    SignalStoreEntry,
    SignalStoreIntegrityError,
    SQLiteSignalStore,
)

from tests.factories import feature, observation, signal

STORED_AT = datetime(2026, 7, 18, 1, 0, tzinfo=UTC)


def _store_with_feature(path: Path) -> SQLiteSignalStore:
    store = SQLiteSignalStore(path)
    store.append_observation(observation())
    store.append_feature(feature())
    return store


def _row_count(path: Path, table: str) -> int:
    with sqlite3.connect(path) as connection:
        row = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    assert row is not None
    return int(row[0])


def _insert_signal_without_store_entry(path: Path, identifier: str) -> None:
    item = signal(identifier=identifier)
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            INSERT INTO signals(
                id, target_type, target_value, signal_type, direction, strength,
                confidence, horizon, observed_at, created_at, producer_version,
                model_version, prompt_version, scorer_version, transformation_version
            ) VALUES (?, 'currency', 'USD', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.signal_id.value,
                item.signal_type,
                item.direction.value,
                item.strength.value,
                item.confidence.value,
                item.horizon.value,
                item.observed_at.isoformat(),
                item.created_at.isoformat(),
                item.versions.producer_version,
                item.versions.model_version,
                item.versions.prompt_version,
                item.versions.scorer_version,
                item.versions.transformation_version,
            ),
        )
        connection.executemany(
            "INSERT INTO signal_sources(signal_id, feature_id) VALUES (?, ?)",
            ((item.signal_id.value, source.value) for source in item.source_feature_ids),
        )


def test_signal_store_entry_is_typed_immutable_and_utc() -> None:
    entry = SignalStoreEntry(
        contract_version=SIGNAL_STORE_ENTRY_VERSION,
        store_sequence=1,
        signal_id=SignalId("signal-1"),
        stored_at=STORED_AT,
        storage_origin=SignalStorageOrigin.APPEND,
    )

    assert entry.store_sequence == 1
    with pytest.raises(FrozenInstanceError):
        entry.store_sequence = 2  # type: ignore[misc]
    with pytest.raises(ValueError, match="positive integer"):
        replace(entry, store_sequence=0)
    with pytest.raises(ValueError, match="positive integer"):
        replace(entry, store_sequence=True)
    with pytest.raises(TypeError, match="SignalId"):
        replace(entry, signal_id="signal-1")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="timezone-aware"):
        replace(entry, stored_at=STORED_AT.replace(tzinfo=None))
    with pytest.raises(ValueError, match="unsupported"):
        replace(entry, contract_version="signal-store-entry-v2")


def test_append_signal_persists_signal_lineage_and_store_entry_atomically(
    tmp_path: Path,
) -> None:
    store = _store_with_feature(tmp_path / "signals.sqlite3")

    store.append_signal(signal(), stored_at=STORED_AT)

    assert store.get_signal(SignalId("signal-1")) == signal()
    assert store.get_lineage(SignalId("signal-1")).feature_ids == (
        signal().source_feature_ids
    )
    assert store.get_signal_store_entry(SignalId("signal-1")) == SignalStoreEntry(
        contract_version=SIGNAL_STORE_ENTRY_VERSION,
        store_sequence=1,
        signal_id=SignalId("signal-1"),
        stored_at=STORED_AT,
        storage_origin=SignalStorageOrigin.APPEND,
    )


def test_committed_store_sequences_are_unique_monotonic_and_define_checkpoint(
    tmp_path: Path,
) -> None:
    store = _store_with_feature(tmp_path / "signals.sqlite3")

    for index in range(1, 4):
        store.append_signal(
            signal(identifier=f"signal-{index}"),
            stored_at=STORED_AT + timedelta(seconds=index),
        )

    entries = tuple(
        store.get_signal_store_entry(SignalId(f"signal-{index}"))
        for index in range(1, 4)
    )
    assert tuple(item.store_sequence for item in entries) == (1, 2, 3)
    assert store.current_signal_checkpoint() == 3


def test_late_old_created_signal_receives_later_store_sequence(tmp_path: Path) -> None:
    store = _store_with_feature(tmp_path / "signals.sqlite3")
    current = signal(identifier="signal-current")
    old = replace(
        signal(identifier="signal-old"),
        observed_at=current.observed_at - timedelta(days=1),
        created_at=current.created_at - timedelta(days=1),
    )

    store.append_signal(current, stored_at=STORED_AT)
    store.append_signal(old, stored_at=STORED_AT + timedelta(seconds=1))

    assert store.get_signal_store_entry(current.signal_id).store_sequence == 1
    assert store.get_signal_store_entry(old.signal_id).store_sequence == 2


def test_duplicate_append_signal_does_not_issue_another_store_sequence(
    tmp_path: Path,
) -> None:
    store = _store_with_feature(tmp_path / "signals.sqlite3")
    store.append_signal(signal(), stored_at=STORED_AT)

    with pytest.raises(sqlite3.IntegrityError):
        store.append_signal(signal(), stored_at=STORED_AT + timedelta(seconds=1))

    assert store.current_signal_checkpoint() == 1
    assert _row_count(store.path, "signal_store_entries") == 1


def test_append_signal_if_absent_preserves_legacy_existing_id_semantics(
    tmp_path: Path,
) -> None:
    store = _store_with_feature(tmp_path / "signals.sqlite3")
    original = signal()
    different_content = replace(original, direction=DirectionScore(-0.5))

    assert store.append_signal_if_absent(original, stored_at=STORED_AT) is True
    first_entry = store.get_signal_store_entry(original.signal_id)
    assert (
        store.append_signal_if_absent(
            different_content,
            stored_at=STORED_AT + timedelta(seconds=1),
        )
        is False
    )

    assert store.get_signal(original.signal_id) == original
    assert store.get_signal_store_entry(original.signal_id) == first_entry
    assert store.current_signal_checkpoint() == 1


def test_missing_feature_rolls_back_signal_lineage_and_store_entry(tmp_path: Path) -> None:
    store = SQLiteSignalStore(tmp_path / "signals.sqlite3")

    with pytest.raises(sqlite3.IntegrityError):
        store.append_signal(signal(feature_id="missing-feature"), stored_at=STORED_AT)

    assert _row_count(store.path, "signals") == 0
    assert _row_count(store.path, "signal_sources") == 0
    assert _row_count(store.path, "signal_store_entries") == 0
    assert store.current_signal_checkpoint() == 0


def test_store_entry_failure_rolls_back_signal_and_feature_lineage(tmp_path: Path) -> None:
    store = _store_with_feature(tmp_path / "signals.sqlite3")
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            """
            CREATE TRIGGER reject_test_signal_store_entry
            BEFORE INSERT ON signal_store_entries
            WHEN NEW.signal_id = 'signal-failed'
            BEGIN SELECT RAISE(ABORT, 'test Store entry failure'); END
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="Store entry failure"):
        store.append_signal(signal(identifier="signal-failed"), stored_at=STORED_AT)

    assert _row_count(store.path, "signals") == 0
    assert _row_count(store.path, "signal_sources") == 0
    assert _row_count(store.path, "signal_store_entries") == 0
    store.append_signal(signal(identifier="signal-ok"), stored_at=STORED_AT)
    assert store.get_signal_store_entry(SignalId("signal-ok")).store_sequence == 1


def test_append_rejects_non_utc_stored_at_before_writing(tmp_path: Path) -> None:
    store = _store_with_feature(tmp_path / "signals.sqlite3")
    non_utc = STORED_AT.astimezone(timezone(timedelta(hours=9)))

    with pytest.raises(ValueError, match="UTC"):
        store.append_signal(signal(), stored_at=non_utc)

    assert _row_count(store.path, "signals") == 0
    assert _row_count(store.path, "signal_store_entries") == 0


def test_existing_signal_without_store_entry_fails_closed(tmp_path: Path) -> None:
    store = _store_with_feature(tmp_path / "signals.sqlite3")
    store.append_signal(signal(), stored_at=STORED_AT)
    with sqlite3.connect(store.path) as connection:
        connection.execute("DROP TRIGGER signal_store_entries_no_delete")
        connection.execute("DELETE FROM signal_store_entries WHERE signal_id = 'signal-1'")

    with pytest.raises(SignalStoreIntegrityError, match="has no Signal Store entry"):
        store.append_signal_if_absent(signal(), stored_at=STORED_AT)


def test_checkpoint_rejects_missing_store_entry_instead_of_returning_partial_max(
    tmp_path: Path,
) -> None:
    store = _store_with_feature(tmp_path / "signals.sqlite3")
    store.append_signal(signal(identifier="signal-sequenced"), stored_at=STORED_AT)
    _insert_signal_without_store_entry(store.path, "signal-unsequenced")

    with pytest.raises(SignalStoreIntegrityError, match="without a Store entry"):
        store.current_signal_checkpoint()


def test_checkpoint_rejects_orphan_store_entry(tmp_path: Path) -> None:
    store = SQLiteSignalStore(tmp_path / "signals.sqlite3")
    with sqlite3.connect(store.path) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute(
            """
            INSERT INTO signal_store_entries(
                contract_version, signal_id, stored_at, storage_origin
            ) VALUES (?, ?, ?, ?)
            """,
            (
                SIGNAL_STORE_ENTRY_VERSION,
                "signal-orphan",
                STORED_AT.isoformat(),
                SignalStorageOrigin.APPEND.value,
            ),
        )

    with pytest.raises(SignalStoreIntegrityError, match="without a Signal"):
        store.current_signal_checkpoint()


def test_checkpoint_rejects_store_entry_that_cannot_be_hydrated(tmp_path: Path) -> None:
    store = _store_with_feature(tmp_path / "signals.sqlite3")
    store.append_signal(signal(), stored_at=STORED_AT)
    with sqlite3.connect(store.path) as connection:
        connection.execute("DROP TRIGGER signal_store_entries_no_update")
        connection.execute(
            "UPDATE signal_store_entries SET stored_at = 'not-a-datetime'"
        )

    with pytest.raises(SignalStoreIntegrityError, match="invalid Store entry"):
        store.current_signal_checkpoint()


class _ConnectionCountingStore(SQLiteSignalStore):
    connection_count: int = 0

    def _connect(self) -> sqlite3.Connection:
        self.connection_count += 1
        return super()._connect()


def test_list_signals_hydrates_all_rows_with_one_connection(tmp_path: Path) -> None:
    store = _ConnectionCountingStore(tmp_path / "signals.sqlite3")
    store.append_observation(observation())
    store.append_feature(feature())
    store.append_signal(signal(identifier="signal-a"), stored_at=STORED_AT)
    store.append_signal(signal(identifier="signal-b"), stored_at=STORED_AT)
    store.connection_count = 0

    assert tuple(item.signal_id.value for item in store.list_signals()) == (
        "signal-a",
        "signal-b",
    )
    assert store.connection_count == 1
