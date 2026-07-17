import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fx_core import SignalId
from fx_signal_store import SignalStorageOrigin, SQLiteSignalStore

from tests.factories import feature, observation, signal
from tests.pair_signal_materialization.factories import NOW, request

ROOT = Path(__file__).parents[2]
MIGRATIONS = ROOT / "packages/fx_signal_store/src/fx_signal_store/migrations"


def _create_0001_legacy_database(
    path: Path,
    rows: tuple[tuple[str, datetime], ...],
) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            (MIGRATIONS / "0001_signal_lineage.sql").read_text(encoding="utf-8")
        )
        connection.execute(
            "CREATE TABLE schema_migrations "
            "(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            ("0001_signal_lineage.sql", datetime(2026, 7, 17, tzinfo=UTC).isoformat()),
        )
        connection.executemany(
            """
            INSERT INTO signals(
                id, target_type, target_value, signal_type, direction, strength,
                confidence, horizon, observed_at, created_at, producer_version,
                model_version, prompt_version, scorer_version, transformation_version
            ) VALUES (?, 'currency', 'USD', 'currency_fundamental', 0.5, 0.7,
                0.8, '3d', ?, ?, 'producer-v1', 'model-v1', 'prompt-v1',
                'scorer-v1', NULL)
            """,
            (
                (identifier, created_at.isoformat(), created_at.isoformat())
                for identifier, created_at in rows
            ),
        )


def _applied_migrations(path: Path) -> tuple[str, ...]:
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
    return tuple(row[0] for row in rows)


def test_fresh_database_applies_exactly_0001_and_0002(tmp_path: Path) -> None:
    store = SQLiteSignalStore(tmp_path / "fresh.sqlite3")

    assert _applied_migrations(store.path) == (
        "0001_signal_lineage.sql",
        "0002_pair_materialization_persistence.sql",
    )
    assert tuple(path.name for path in sorted(MIGRATIONS.glob("*.sql"))) == (
        "0001_signal_lineage.sql",
        "0002_pair_materialization_persistence.sql",
    )


def test_0002_backfills_one_catalog_sequence_per_legacy_signal_in_explicit_order(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy.sqlite3"
    earlier = datetime(2026, 7, 17, 1, 0, tzinfo=UTC)
    later = earlier + timedelta(hours=1)
    _create_0001_legacy_database(
        path,
        (
            ("signal-z", later),
            ("signal-b", earlier),
            ("signal-a", earlier),
        ),
    )

    store = SQLiteSignalStore(path)

    entries = tuple(
        store.get_signal_store_entry(SignalId(identifier))
        for identifier in ("signal-a", "signal-b", "signal-z")
    )
    assert tuple(item.store_sequence for item in entries) == (1, 2, 3)
    assert all(item.storage_origin is SignalStorageOrigin.LEGACY_BACKFILL for item in entries)
    assert all(item.stored_at.tzinfo is not None for item in entries)
    assert all(item.stored_at.utcoffset() == UTC.utcoffset(item.stored_at) for item in entries)
    assert store.current_signal_checkpoint() == 3


def test_reopen_and_migrate_rerun_do_not_duplicate_legacy_entries(tmp_path: Path) -> None:
    path = tmp_path / "legacy.sqlite3"
    _create_0001_legacy_database(
        path,
        (("signal-1", datetime(2026, 7, 17, tzinfo=UTC)),),
    )

    first = SQLiteSignalStore(path)
    first.migrate()
    reopened = SQLiteSignalStore(path)

    with sqlite3.connect(path) as connection:
        count = connection.execute("SELECT COUNT(*) FROM signal_store_entries").fetchone()
    assert count == (1,)
    assert reopened.get_signal_store_entry(SignalId("signal-1")).store_sequence == 1


def test_new_evidence_tables_reject_update_and_delete(tmp_path: Path) -> None:
    store = SQLiteSignalStore(tmp_path / "immutable.sqlite3")
    store.append_observation(observation())
    store.append_feature(feature())
    store.append_signal(signal(), stored_at=NOW)
    store.claim_pair_signal_materialization(
        request(),
        captured_at=NOW + timedelta(minutes=1),
    )
    mutations = (
        ("signal_store_entries", "stored_at = stored_at", "store_sequence = 1"),
        (
            "pair_signal_materialization_specifications",
            "producer_version = producer_version",
            "specification_id = specification_id",
        ),
        (
            "pair_signal_materialization_requests",
            "as_of = as_of",
            "request_id = request_id",
        ),
        (
            "pair_signal_materialization_claims",
            "captured_at = captured_at",
            "request_id = request_id",
        ),
    )

    for table, assignment, predicate in mutations:
        with sqlite3.connect(store.path) as connection, pytest.raises(
            sqlite3.IntegrityError, match="immutable"
        ):
            connection.execute(f"UPDATE {table} SET {assignment} WHERE {predicate}")
        with sqlite3.connect(store.path) as connection, pytest.raises(
            sqlite3.IntegrityError, match="immutable"
        ):
            connection.execute(f"DELETE FROM {table} WHERE {predicate}")


def test_0002_adds_claim_only_and_no_terminal_pair_artifact_tables(
    tmp_path: Path,
) -> None:
    store = SQLiteSignalStore(tmp_path / "scope.sqlite3")
    with sqlite3.connect(store.path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }

    assert {
        "signal_store_entries",
        "pair_signal_materialization_specifications",
        "pair_signal_materialization_requests",
        "pair_signal_materialization_claims",
    } <= tables
    assert {
        "pair_signal_selection_snapshots",
        "pair_signal_selection_candidates",
        "pair_signal_derivations",
        "pair_signal_materialization_completions",
    }.isdisjoint(tables)


def test_signal_storage_origin_reserves_pair_materialization_without_using_it() -> None:
    assert tuple(item.value for item in SignalStorageOrigin) == (
        "LEGACY_BACKFILL",
        "APPEND",
        "PAIR_MATERIALIZATION",
    )
