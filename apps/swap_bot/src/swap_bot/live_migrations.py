import sqlite3
from datetime import UTC, datetime
from importlib.resources import files


def migrate_live_database(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS live_schema_migrations "
        "(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    connection.commit()
    migration_root = files("swap_bot").joinpath("migrations")
    for migration in sorted(migration_root.iterdir(), key=lambda item: item.name):
        if not migration.name.endswith(".sql"):
            continue
        _apply_migration_exact(
            connection,
            migration_name=migration.name,
            migration_sql=migration.read_text(encoding="utf-8"),
        )


def _apply_migration_exact(
    connection: sqlite3.Connection, *, migration_name: str, migration_sql: str
) -> None:
    """Apply one numbered migration and its marker as one SQLite writer transaction."""
    try:
        connection.execute("BEGIN IMMEDIATE")
        exists = connection.execute(
            "SELECT 1 FROM live_schema_migrations WHERE version = ?", (migration_name,)
        ).fetchone()
        if exists is not None:
            connection.commit()
            return
        for statement in _sql_statements(migration_sql):
            connection.execute(statement)
        connection.execute(
            "INSERT INTO live_schema_migrations(version, applied_at) VALUES (?, ?)",
            (migration_name, datetime.now(UTC).isoformat()),
        )
        connection.commit()
    except BaseException:
        if connection.in_transaction:
            connection.rollback()
        raise


def _sql_statements(script: str) -> tuple[str, ...]:
    statements: list[str] = []
    pending = ""
    for character in script:
        pending += character
        if sqlite3.complete_statement(pending):
            statement = pending.strip()
            if statement:
                statements.append(statement)
            pending = ""
    if pending.strip():
        raise ValueError("migration SQL ends with an incomplete statement")
    return tuple(statements)
