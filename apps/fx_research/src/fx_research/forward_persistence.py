import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from decimal import Decimal
from importlib.resources import files
from pathlib import Path

from fx_core import CurrencyPair, Horizon, SignalId

from .forward import (
    ForwardJobRecord,
    ForwardJobStatus,
    ForwardObservationJob,
    ForwardProjection,
    ForwardResult,
    MarketCandle,
    MarketSnapshot,
    UnavailableReason,
)
from .persistence import safe_error_message


class SQLiteForwardEvaluationStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.migrate()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
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
                for row in connection.execute(
                    "SELECT version FROM research_schema_migrations"
                )
            }
            for migration in sorted(migration_root.iterdir(), key=lambda item: item.name):
                if not migration.name.endswith(".sql") or migration.name in applied:
                    continue
                connection.executescript(migration.read_text(encoding="utf-8"))
                connection.execute(
                    "INSERT INTO research_schema_migrations VALUES (?, ?)",
                    (migration.name, datetime.now(UTC).isoformat()),
                )

    def append_jobs(
        self, jobs: tuple[ForwardObservationJob, ...], *, scheduled_at: datetime
    ) -> int:
        inserted = 0
        with closing(self._connect()) as connection, connection:
            for job in jobs:
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO research_forward_jobs VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        'PENDING', NULL, NULL, NULL, NULL, ?
                    )
                    """,
                    self._job_values(job) + (scheduled_at.isoformat(),),
                )
                inserted += cursor.rowcount
        return inserted

    def get_job(self, job_id: str) -> ForwardJobRecord:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM research_forward_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        if row is None:
            raise KeyError(job_id)
        return self._job_record(row)

    def list_jobs(
        self, *, statuses: tuple[ForwardJobStatus, ...] | None = None
    ) -> tuple[ForwardJobRecord, ...]:
        parameters: tuple[str, ...] = ()
        where = ""
        if statuses:
            placeholders = ", ".join("?" for _ in statuses)
            where = f" WHERE status IN ({placeholders})"
            parameters = tuple(status.value for status in statuses)
        with closing(self._connect()) as connection:
            rows = connection.execute(
                f"SELECT * FROM research_forward_jobs{where} "
                "ORDER BY target_at, signal_id, horizon",
                parameters,
            ).fetchall()
        return tuple(self._job_record(row) for row in rows)

    def mark_failed(
        self, job_id: str, *, error: Exception, updated_at: datetime
    ) -> None:
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                """
                UPDATE research_forward_jobs
                SET status = 'FAILED', unavailable_reason = NULL,
                    error_code = ?, error_message = ?, result_id = NULL, updated_at = ?
                WHERE job_id = ? AND status != 'COMPLETED'
                """,
                (
                    type(error).__name__,
                    safe_error_message(error),
                    updated_at.isoformat(),
                    job_id,
                ),
            )
        if cursor.rowcount != 1:
            raise KeyError(job_id)

    def mark_unavailable(
        self,
        job_id: str,
        *,
        reason: UnavailableReason,
        updated_at: datetime,
    ) -> None:
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                """
                UPDATE research_forward_jobs
                SET status = 'UNAVAILABLE', unavailable_reason = ?,
                    error_code = NULL, error_message = NULL, result_id = NULL, updated_at = ?
                WHERE job_id = ? AND status != 'COMPLETED'
                """,
                (reason.value, updated_at.isoformat(), job_id),
            )
        if cursor.rowcount != 1:
            raise KeyError(job_id)

    def complete(
        self,
        job_id: str,
        *,
        snapshot: MarketSnapshot,
        result: ForwardResult,
    ) -> bool:
        if result.snapshot_id != snapshot.snapshot_id:
            raise ValueError("ForwardResult must reference the supplied MarketSnapshot")
        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT * FROM research_forward_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if row is None:
                raise KeyError(job_id)
            record = self._job_record(row)
            self._validate_completion(record.job, result, snapshot)
            if record.status is ForwardJobStatus.COMPLETED:
                if record.result_id != result.result_id:
                    raise ValueError("completed forward job references another result")
                return False
            self._append_snapshot(connection, snapshot, captured_at=result.completed_at)
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO research_forward_results VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                self._result_values(result),
            )
            existing = connection.execute(
                "SELECT result_id FROM research_forward_results WHERE result_id = ?",
                (result.result_id,),
            ).fetchone()
            if existing is None:
                raise ValueError("ForwardResult semantic identity already has another result")
            connection.execute(
                """
                UPDATE research_forward_jobs
                SET status = 'COMPLETED', unavailable_reason = NULL,
                    error_code = NULL, error_message = NULL, result_id = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (result.result_id, result.completed_at.isoformat(), job_id),
            )
        return cursor.rowcount == 1

    @staticmethod
    def _validate_completion(
        job: ForwardObservationJob,
        result: ForwardResult,
        snapshot: MarketSnapshot,
    ) -> None:
        job_semantics = (
            job.signal_id,
            job.horizon,
            job.projection.instrument,
            job.projection.sign,
            job.projection.version,
            job.anchor_at,
            job.target_at,
            job.market_source,
            job.market_data_version,
            job.price_basis,
            job.granularity,
            job.formula_version,
        )
        result_semantics = (
            result.signal_id,
            result.horizon,
            result.instrument,
            result.projection_sign,
            result.projection_version,
            result.anchor_at,
            result.target_at,
            result.market_source,
            result.market_data_version,
            result.price_basis,
            result.granularity,
            result.formula_version,
        )
        if result_semantics != job_semantics:
            raise ValueError("ForwardResult semantics do not match its persisted job")
        market_semantics = (
            result.market_source,
            result.instrument,
            result.granularity,
            result.price_basis,
            result.market_data_version,
        )
        if any(
            (
                candle.source,
                candle.instrument,
                candle.granularity,
                candle.price_basis,
                candle.market_data_version,
            )
            != market_semantics
            for candle in snapshot.candles
        ):
            raise ValueError("MarketSnapshot semantics do not match its ForwardResult")
        if (
            snapshot.candles[0].open_time != result.t0_observed_at
            or snapshot.candles[0].open != result.price_t0
            or snapshot.candles[-1].open_time != result.tx_observed_at
            or snapshot.candles[-1].open != result.price_tx
        ):
            raise ValueError("MarketSnapshot boundaries do not match its ForwardResult")

    def get_snapshot(self, snapshot_id: str) -> MarketSnapshot:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT candle.*
                FROM research_market_snapshot_candles AS link
                JOIN research_market_candles AS candle
                  ON candle.revision_id = link.candle_revision_id
                WHERE link.snapshot_id = ?
                ORDER BY link.ordinal
                """,
                (snapshot_id,),
            ).fetchall()
        if not rows:
            raise KeyError(snapshot_id)
        return MarketSnapshot(tuple(self._candle(row) for row in rows))

    def get_result(self, result_id: str) -> ForwardResult:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM research_forward_results WHERE result_id = ?", (result_id,)
            ).fetchone()
        if row is None:
            raise KeyError(result_id)
        return self._result(row)

    def list_results(self, *, signal_id: SignalId | None = None) -> tuple[ForwardResult, ...]:
        where = " WHERE signal_id = ?" if signal_id else ""
        parameters = (signal_id.value,) if signal_id else ()
        with closing(self._connect()) as connection:
            rows = connection.execute(
                f"SELECT * FROM research_forward_results{where} "
                "ORDER BY completed_at, result_id",
                parameters,
            ).fetchall()
        return tuple(self._result(row) for row in rows)

    @staticmethod
    def _append_snapshot(
        connection: sqlite3.Connection,
        snapshot: MarketSnapshot,
        *,
        captured_at: datetime,
    ) -> None:
        for candle in snapshot.candles:
            connection.execute(
                "INSERT OR IGNORE INTO research_market_candles VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    candle.revision_id,
                    candle.source,
                    candle.instrument.symbol,
                    candle.granularity,
                    candle.price_basis,
                    candle.open_time.isoformat(),
                    str(candle.open),
                    str(candle.high),
                    str(candle.low),
                    str(candle.close),
                    int(candle.complete),
                    candle.market_data_version,
                ),
            )
        connection.execute(
            "INSERT OR IGNORE INTO research_market_snapshots VALUES (?, ?)",
            (snapshot.snapshot_id, captured_at.isoformat()),
        )
        connection.executemany(
            "INSERT OR IGNORE INTO research_market_snapshot_candles VALUES (?, ?, ?)",
            (
                (snapshot.snapshot_id, ordinal, candle.revision_id)
                for ordinal, candle in enumerate(snapshot.candles)
            ),
        )

    @staticmethod
    def _job_values(job: ForwardObservationJob) -> tuple[object, ...]:
        return (
            job.job_id,
            job.signal_id.value,
            job.horizon.value,
            job.projection.instrument.symbol,
            job.projection.sign,
            job.projection.version,
            job.anchor_at.isoformat(),
            job.target_at.isoformat(),
            job.market_source,
            job.granularity,
            job.price_basis,
            job.market_data_version,
            job.formula_version,
        )

    @staticmethod
    def _job_record(row: sqlite3.Row) -> ForwardJobRecord:
        job = ForwardObservationJob(
            job_id=row["job_id"],
            signal_id=SignalId(row["signal_id"]),
            horizon=Horizon(row["horizon"]),
            projection=ForwardProjection(
                CurrencyPair.parse(row["instrument"]),
                int(row["projection_sign"]),
                row["projection_version"],
            ),
            anchor_at=datetime.fromisoformat(row["anchor_at"]),
            target_at=datetime.fromisoformat(row["target_at"]),
            market_source=row["market_source"],
            granularity=row["granularity"],
            price_basis=row["price_basis"],
            market_data_version=row["market_data_version"],
            formula_version=row["formula_version"],
        )
        return ForwardJobRecord(
            job=job,
            status=ForwardJobStatus(row["status"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            unavailable_reason=UnavailableReason(row["unavailable_reason"])
            if row["unavailable_reason"]
            else None,
            error_code=row["error_code"],
            error_message=row["error_message"],
            result_id=row["result_id"],
        )

    @staticmethod
    def _result_values(result: ForwardResult) -> tuple[object, ...]:
        return (
            result.result_id,
            result.signal_id.value,
            result.horizon.value,
            result.instrument.symbol,
            result.projection_sign,
            result.projection_version,
            result.anchor_at.isoformat(),
            result.target_at.isoformat(),
            str(result.price_t0),
            str(result.price_tx),
            result.t0_observed_at.isoformat(),
            result.tx_observed_at.isoformat(),
            str(result.target_return_bps),
            str(result.mfe_bps) if result.mfe_bps is not None else None,
            str(result.mae_bps) if result.mae_bps is not None else None,
            result.realized_volatility,
            result.completed_at.isoformat(),
            result.market_source,
            result.market_data_version,
            result.price_basis,
            result.granularity,
            result.formula_version,
            result.snapshot_id,
        )

    @staticmethod
    def _result(row: sqlite3.Row) -> ForwardResult:
        return ForwardResult(
            result_id=row["result_id"],
            signal_id=SignalId(row["signal_id"]),
            horizon=Horizon(row["horizon"]),
            instrument=CurrencyPair.parse(row["instrument"]),
            projection_sign=int(row["projection_sign"]),
            projection_version=row["projection_version"],
            anchor_at=datetime.fromisoformat(row["anchor_at"]),
            target_at=datetime.fromisoformat(row["target_at"]),
            price_t0=Decimal(row["price_t0"]),
            price_tx=Decimal(row["price_tx"]),
            t0_observed_at=datetime.fromisoformat(row["t0_observed_at"]),
            tx_observed_at=datetime.fromisoformat(row["tx_observed_at"]),
            target_return_bps=Decimal(row["target_return_bps"]),
            mfe_bps=Decimal(row["mfe_bps"]) if row["mfe_bps"] is not None else None,
            mae_bps=Decimal(row["mae_bps"]) if row["mae_bps"] is not None else None,
            realized_volatility=float(row["realized_volatility"]),
            completed_at=datetime.fromisoformat(row["completed_at"]),
            market_source=row["market_source"],
            market_data_version=row["market_data_version"],
            price_basis=row["price_basis"],
            granularity=row["granularity"],
            formula_version=row["formula_version"],
            snapshot_id=row["snapshot_id"],
        )

    @staticmethod
    def _candle(row: sqlite3.Row) -> MarketCandle:
        candle = MarketCandle(
            source=row["source"],
            instrument=CurrencyPair.parse(row["instrument"]),
            granularity=row["granularity"],
            price_basis=row["price_basis"],
            open_time=datetime.fromisoformat(row["open_time"]),
            open=Decimal(row["open_price"]),
            high=Decimal(row["high_price"]),
            low=Decimal(row["low_price"]),
            close=Decimal(row["close_price"]),
            complete=bool(row["complete"]),
            market_data_version=row["market_data_version"],
        )
        if candle.revision_id != row["revision_id"]:
            raise ValueError("persisted candle revision does not match its content")
        return candle
