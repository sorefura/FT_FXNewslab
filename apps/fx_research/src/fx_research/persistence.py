import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path

from fx_core import Currency, ObservationId


@dataclass(frozen=True, slots=True)
class PendingProductionItem:
    observation_id: ObservationId
    currency: Currency


@dataclass(frozen=True, slots=True)
class IngestionEvidence:
    observation_id: ObservationId
    source_id: str
    currency: Currency
    canonical_url: str
    content_hash: str
    source_date_text: str | None
    first_seen_at: datetime


class SQLiteIngestionStateStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.migrate()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def migrate(self) -> None:
        migration_root = files("fx_research").joinpath("migrations")
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS research_schema_migrations "
                "(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            applied = {
                row[0]
                for row in connection.execute("SELECT version FROM research_schema_migrations")
            }
            for migration in sorted(migration_root.iterdir(), key=lambda item: item.name):
                if not migration.name.endswith(".sql") or migration.name in applied:
                    continue
                connection.executescript(migration.read_text(encoding="utf-8"))
                connection.execute(
                    "INSERT INTO research_schema_migrations VALUES (?, ?)",
                    (migration.name, datetime.now(UTC).isoformat()),
                )

    def first_seen_at(
        self,
        *,
        observation_id: ObservationId,
        source_id: str,
        currency: Currency,
        canonical_url: str,
        content_hash: str,
        source_date_text: str | None,
        recognized_at: datetime,
    ) -> datetime:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO research_ingestion_items
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    observation_id.value,
                    source_id,
                    currency.code,
                    canonical_url,
                    content_hash,
                    source_date_text,
                    recognized_at.isoformat(),
                ),
            )
            row = connection.execute(
                "SELECT first_seen_at FROM research_ingestion_items WHERE observation_id = ?",
                (observation_id.value,),
            ).fetchone()
        if row is None:
            raise RuntimeError("Failed to persist ingestion identity")
        return datetime.fromisoformat(row["first_seen_at"])

    def record_fetch(
        self,
        *,
        source_id: str,
        fetched_at: datetime,
        status: str,
        item_count: int,
        error: Exception | None = None,
    ) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "INSERT INTO research_fetch_runs(source_id, fetched_at, status, item_count, "
                "error_code, error_message) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    source_id,
                    fetched_at.isoformat(),
                    status,
                    item_count,
                    type(error).__name__ if error else None,
                    str(error) if error else None,
                ),
            )

    def pending_items(
        self, *, producer_version: str, model_version: str, prompt_version: str
    ) -> tuple[PendingProductionItem, ...]:
        with closing(self._connect()) as connection, connection:
            rows = connection.execute(
                """
                SELECT item.observation_id, item.candidate_currency
                FROM research_ingestion_items AS item
                LEFT JOIN research_feature_jobs AS job
                  ON job.observation_id = item.observation_id
                 AND job.producer_version = ?
                 AND job.model_version = ?
                 AND job.prompt_version = ?
                 AND job.status = 'COMPLETED'
                WHERE job.observation_id IS NULL
                ORDER BY item.first_seen_at, item.observation_id
                """,
                (producer_version, model_version, prompt_version),
            ).fetchall()
        return tuple(
            PendingProductionItem(
                ObservationId(row["observation_id"]), Currency(row["candidate_currency"])
            )
            for row in rows
        )

    def get_ingestion_evidence(self, observation_id: ObservationId) -> IngestionEvidence:
        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT * FROM research_ingestion_items WHERE observation_id = ?",
                (observation_id.value,),
            ).fetchone()
        if row is None:
            raise KeyError(observation_id.value)
        return IngestionEvidence(
            observation_id=observation_id,
            source_id=row["source_id"],
            currency=Currency(row["candidate_currency"]),
            canonical_url=row["canonical_url"],
            content_hash=row["content_hash"],
            source_date_text=row["source_date_text"],
            first_seen_at=datetime.fromisoformat(row["first_seen_at"]),
        )

    def record_production(
        self,
        *,
        observation_id: ObservationId,
        producer_version: str,
        model_version: str,
        prompt_version: str,
        status: str,
        updated_at: datetime,
        feature_id: str | None = None,
        signal_id: str | None = None,
        error: Exception | None = None,
    ) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO research_feature_jobs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(observation_id, producer_version, model_version, prompt_version)
                DO UPDATE SET status=excluded.status, feature_id=excluded.feature_id,
                    signal_id=excluded.signal_id, error_code=excluded.error_code,
                    error_message=excluded.error_message, updated_at=excluded.updated_at
                """,
                (
                    observation_id.value,
                    producer_version,
                    model_version,
                    prompt_version,
                    status,
                    feature_id,
                    signal_id,
                    type(error).__name__ if error else None,
                    str(error) if error else None,
                    updated_at.isoformat(),
                ),
            )
