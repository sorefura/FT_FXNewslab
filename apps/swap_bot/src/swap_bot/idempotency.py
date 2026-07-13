import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path


class SQLiteIdempotencyStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS execution_idempotency "
                "(key TEXT PRIMARY KEY, claimed_at TEXT NOT NULL)"
            )

    def claim(self, key: str) -> bool:
        if not key.strip():
            raise ValueError("idempotency key must not be blank")
        try:
            with closing(sqlite3.connect(self.path)) as connection, connection:
                connection.execute(
                    "INSERT INTO execution_idempotency(key, claimed_at) VALUES (?, ?)",
                    (key, datetime.now(UTC).isoformat()),
                )
        except sqlite3.IntegrityError:
            return False
        return True
