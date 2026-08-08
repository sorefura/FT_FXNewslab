import sqlite3
from concurrent.futures import ThreadPoolExecutor
from importlib.resources import files
from pathlib import Path

import pytest
from swap_bot.decision_store import _SCHEMA
from swap_bot.live_migrations import _apply_migration_exact
from swap_bot.paper import SQLitePaperStore

from tests.paper_persistence._helpers import populate_full_flow

_B6_SEQUENCE = (
    "0001_validated_signal_live_adoption.sql",
    "0002_candidate_authorization_integrity.sql",
    "0003_operational_swap_evidence.sql",
    "0004_production_entry_strategy.sql",
    "0005_ordinary_close_path.sql",
    "0006_paper_execution_ledger.sql",
)

_ALL_PAPER_TABLES = (
    "live_paper_market_observations",
    "live_paper_fill_policies",
    "live_paper_account_bootstraps",
    "live_paper_orders",
    "live_paper_order_events",
    "live_paper_fill_evaluation_plans",
    "live_paper_fill_evaluation_steps",
    "live_paper_fill_evaluation_attempts",
    "live_paper_step_terminal_claims",
    "live_paper_market_observation_selections",
    "live_paper_no_market_outcomes",
    "live_paper_fills",
    "live_paper_positions",
    "live_paper_position_fill_applications",
    "live_paper_position_snapshots",
    "live_paper_account_snapshots",
    "live_paper_ledger_entries",
    "live_paper_swap_rollover_claims",
    "live_paper_swap_accruals",
    "live_paper_swap_non_accruals",
    "live_paper_swap_accrual_corrections",
    "live_paper_reservation_consumptions",
    "live_paper_reservation_releases",
    "live_paper_reconciliation_results",
)


def _versions(path: Path) -> tuple[str, ...]:
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            "SELECT version FROM live_schema_migrations ORDER BY version"
        ).fetchall()
    return tuple(row[0] for row in rows)


def test_fresh_database_and_reopen_converge_through_0006(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    with sqlite3.connect(path) as connection:
        connection.executescript(_SCHEMA)

    SQLitePaperStore(path)
    SQLitePaperStore(path)

    assert _versions(path) == _B6_SEQUENCE


def test_upgrade_from_0005_database_converges_through_0006(tmp_path: Path) -> None:
    path = tmp_path / "legacy.sqlite"
    with sqlite3.connect(path) as connection:
        connection.executescript(_SCHEMA)
        migration_root = files("swap_bot").joinpath("migrations")
        for migration_name in _B6_SEQUENCE[:-1]:
            connection.executescript(
                migration_root.joinpath(migration_name).read_text(encoding="utf-8")
            )
        connection.execute(
            "CREATE TABLE live_schema_migrations "
            "(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        for version in _B6_SEQUENCE[:-1]:
            connection.execute(
                "INSERT INTO live_schema_migrations VALUES (?, '2026-08-07T00:00:00+00:00')",
                (version,),
            )

    SQLitePaperStore(path)

    assert _versions(path) == _B6_SEQUENCE


def test_migration_body_failure_rolls_back_body_and_marker_and_retries(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE live_schema_migrations "
            "(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        with pytest.raises(sqlite3.OperationalError):
            _apply_migration_exact(
                connection,
                migration_name="9998_paper_failure.sql",
                migration_sql=(
                    "CREATE TABLE paper_body_failure (id INTEGER); INSERT INTO absent VALUES (1);"
                ),
            )
        assert (
            connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name = 'paper_body_failure'"
            ).fetchone()
            is None
        )
        assert (
            connection.execute(
                "SELECT 1 FROM live_schema_migrations WHERE version = '9998_paper_failure.sql'"
            ).fetchone()
            is None
        )
        _apply_migration_exact(
            connection,
            migration_name="9998_paper_failure.sql",
            migration_sql="CREATE TABLE paper_body_failure (id INTEGER);",
        )
    assert _versions(path) == ("9998_paper_failure.sql",)


def test_concurrent_initializers_converge_on_one_marker_per_migration(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    with sqlite3.connect(path) as connection:
        connection.executescript(_SCHEMA)

    with ThreadPoolExecutor(max_workers=2) as executor:
        tuple(executor.map(lambda _: SQLitePaperStore(path), range(2)))

    assert _versions(path) == _B6_SEQUENCE


@pytest.fixture(scope="module")
def _populated_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("paper-triggers") / "live.sqlite"
    populate_full_flow(path)
    return path


@pytest.mark.parametrize("table", _ALL_PAPER_TABLES)
def test_every_paper_table_rejects_update_and_delete(_populated_db: Path, table: str) -> None:
    with sqlite3.connect(_populated_db) as connection:
        columns = [row[1] for row in connection.execute(f"PRAGMA table_info({table})")]
        assert columns, f"expected {table} to have columns"
        count = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        assert count >= 1, f"expected {table} to hold at least one populated row"
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(f"UPDATE {table} SET {columns[0]} = {columns[0]}")
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(f"DELETE FROM {table}")
