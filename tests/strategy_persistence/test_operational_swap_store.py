import sqlite3
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from importlib.resources import files
from pathlib import Path

import pytest
from swap_bot.decision_store import _SCHEMA
from swap_bot.live_migrations import _apply_migration_exact
from swap_bot.operational_swap import (
    OperationalSwapAppendDisposition,
    OperationalSwapIntegrityError,
    OperationalSwapReadDisposition,
    SQLiteOperationalSwapStore,
)

from tests.strategy_contracts.factories import swap_evidence


def _versions(path: Path) -> tuple[str, ...]:
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            "SELECT version FROM live_schema_migrations ORDER BY version"
        ).fetchall()
    return tuple(row[0] for row in rows)


def test_live_migrations_create_and_reopen_the_exact_b4_sequence(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"

    SQLiteOperationalSwapStore(path)
    SQLiteOperationalSwapStore(path)

    assert _versions(path) == (
        "0001_validated_signal_live_adoption.sql",
        "0002_candidate_authorization_integrity.sql",
        "0003_operational_swap_evidence.sql",
        "0004_production_entry_strategy.sql",
        "0005_ordinary_close_path.sql",
    )


def test_legacy_0002_database_upgrades_through_b4_migrations(tmp_path: Path) -> None:
    path = tmp_path / "legacy.sqlite"
    with sqlite3.connect(path) as connection:
        connection.executescript(_SCHEMA)
        migration_root = files("swap_bot").joinpath("migrations")
        for migration_name in (
            "0001_validated_signal_live_adoption.sql",
            "0002_candidate_authorization_integrity.sql",
        ):
            connection.executescript(
                migration_root.joinpath(migration_name).read_text(encoding="utf-8")
            )
        connection.execute(
            "CREATE TABLE live_schema_migrations "
            "(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        for version in (
            "0001_validated_signal_live_adoption.sql",
            "0002_candidate_authorization_integrity.sql",
        ):
            connection.execute(
                "INSERT INTO live_schema_migrations VALUES (?, '2026-07-18T00:00:00+00:00')",
                (version,),
            )

    SQLiteOperationalSwapStore(path)

    assert _versions(path) == (
        "0001_validated_signal_live_adoption.sql",
        "0002_candidate_authorization_integrity.sql",
        "0003_operational_swap_evidence.sql",
        "0004_production_entry_strategy.sql",
        "0005_ordinary_close_path.sql",
    )


def test_live_migration_body_failure_rolls_back_body_and_marker(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE live_schema_migrations "
            "(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        with pytest.raises(sqlite3.OperationalError):
            _apply_migration_exact(
                connection,
                migration_name="9998_failure.sql",
                migration_sql=(
                    "CREATE TABLE body_failure (id INTEGER); "
                    "INSERT INTO absent VALUES (1);"
                ),
            )
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'body_failure'"
        ).fetchone() is None
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name = 'live_schema_migrations'"
        ).fetchone() is not None
        assert connection.execute(
            "SELECT 1 FROM live_schema_migrations WHERE version = '9998_failure.sql'"
        ).fetchone() is None
        _apply_migration_exact(
            connection,
            migration_name="9998_failure.sql",
            migration_sql="CREATE TABLE body_failure (id INTEGER);",
        )

    assert _versions(path) == ("9998_failure.sql",)


def test_live_migration_marker_failure_rolls_back_body(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE live_schema_migrations "
            "(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TRIGGER reject_marker BEFORE INSERT ON live_schema_migrations "
            "BEGIN SELECT RAISE(ABORT, 'marker failure'); END"
        )
        with pytest.raises(sqlite3.IntegrityError, match="marker failure"):
            _apply_migration_exact(
                connection,
                migration_name="9997_marker_failure.sql",
                migration_sql="CREATE TABLE marker_failure (id INTEGER);",
            )
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'marker_failure'"
        ).fetchone() is None


def test_concurrent_live_initializers_converge_on_one_marker_per_migration(
    tmp_path: Path,
) -> None:
    path = tmp_path / "live.sqlite"

    with ThreadPoolExecutor(max_workers=2) as executor:
        tuple(executor.map(lambda _: SQLiteOperationalSwapStore(path), range(2)))

    assert _versions(path) == (
        "0001_validated_signal_live_adoption.sql",
        "0002_candidate_authorization_integrity.sql",
        "0003_operational_swap_evidence.sql",
        "0004_production_entry_strategy.sql",
        "0005_ordinary_close_path.sql",
    )


def test_store_appends_reuses_and_hydrates_exact_signed_zero(tmp_path: Path) -> None:
    store = SQLiteOperationalSwapStore(tmp_path / "live.sqlite")
    evidence = swap_evidence(long_received_amount=Decimal("-0"))

    inserted = store.append_or_compare(evidence)
    reused = store.append_or_compare(evidence)
    found = store.get_exact(evidence.swap_evidence_id)

    assert inserted.disposition is OperationalSwapAppendDisposition.INSERTED
    assert reused.disposition is OperationalSwapAppendDisposition.REUSED_IDENTICAL
    assert found.disposition is OperationalSwapReadDisposition.FOUND
    assert found.evidence == evidence
    assert str(found.evidence.long_received_amount) == "-0"


def test_store_returns_typed_missing_for_an_absent_exact_id(tmp_path: Path) -> None:
    store = SQLiteOperationalSwapStore(tmp_path / "live.sqlite")

    result = store.get_exact("swap-evidence-missing")

    assert result.disposition is OperationalSwapReadDisposition.MISSING
    assert result.evidence is None


def test_store_treats_conflicting_persisted_content_as_an_integrity_error(
    tmp_path: Path,
) -> None:
    store = SQLiteOperationalSwapStore(tmp_path / "live.sqlite")
    evidence = swap_evidence()
    store.append_or_compare(evidence)

    with sqlite3.connect(store.path) as connection:
        connection.execute("DROP TRIGGER live_operational_swap_evidence_no_update")
        connection.execute(
            "UPDATE live_operational_swap_evidence SET source = 'conflicting-source' "
            "WHERE swap_evidence_id = ?",
            (evidence.swap_evidence_id,),
        )

    with pytest.raises(OperationalSwapIntegrityError):
        store.append_or_compare(evidence)


def test_store_treats_corrupt_persisted_content_as_an_integrity_error(
    tmp_path: Path,
) -> None:
    store = SQLiteOperationalSwapStore(tmp_path / "live.sqlite")
    evidence = swap_evidence()
    store.append_or_compare(evidence)

    with sqlite3.connect(store.path) as connection:
        connection.execute("DROP TRIGGER live_operational_swap_evidence_no_update")
        connection.execute(
            "UPDATE live_operational_swap_evidence SET long_received_amount = 'not-decimal' "
            "WHERE swap_evidence_id = ?",
            (evidence.swap_evidence_id,),
        )

    with pytest.raises(OperationalSwapIntegrityError):
        store.get_exact(evidence.swap_evidence_id)


def test_operational_swap_rows_reject_update_and_delete(tmp_path: Path) -> None:
    store = SQLiteOperationalSwapStore(tmp_path / "live.sqlite")
    evidence = swap_evidence()
    store.append_or_compare(evidence)

    with sqlite3.connect(store.path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "UPDATE live_operational_swap_evidence SET source = 'other' "
                "WHERE swap_evidence_id = ?",
                (evidence.swap_evidence_id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "DELETE FROM live_operational_swap_evidence WHERE swap_evidence_id = ?",
                (evidence.swap_evidence_id,),
            )
