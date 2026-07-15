import sqlite3
from datetime import UTC, datetime
from importlib.resources import files


def migrate_live_database(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS live_schema_migrations "
        "(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    applied = {
        row[0]
        for row in connection.execute("SELECT version FROM live_schema_migrations")
    }
    migration_root = files("swap_bot").joinpath("migrations")
    for migration in sorted(migration_root.iterdir(), key=lambda item: item.name):
        if not migration.name.endswith(".sql") or migration.name in applied:
            continue
        connection.executescript(migration.read_text(encoding="utf-8"))
        connection.execute(
            "INSERT INTO live_schema_migrations(version, applied_at) VALUES (?, ?)",
            (migration.name, datetime.now(UTC).isoformat()),
        )
