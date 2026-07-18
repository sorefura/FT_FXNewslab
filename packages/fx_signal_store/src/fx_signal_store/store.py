import json
import sqlite3
from collections.abc import Iterable
from contextlib import closing
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from pathlib import Path

from fx_core import (
    Currency,
    CurrencyFundamentalFeature,
    CurrencyPair,
    CurrencyTarget,
    DirectionScore,
    FactorScore,
    FeatureId,
    FundamentalFactor,
    Horizon,
    NewsObservation,
    ObservationId,
    PairScore,
    PairTarget,
    Probability,
    Signal,
    SignalId,
    VersionMetadata,
)
from fx_core.time import require_utc

from .pair_materialization import (
    PairSignalCandidateEligibility,
    PairSignalCandidateRejectionReason,
    PairSignalMaterializationRequest,
    PairSignalMaterializationSpecification,
    PairSignalSelectionCandidate,
    PairSignalSelectionOutcome,
    PairSignalSelectionReason,
    PairSignalSelectionSnapshot,
    SignalContentSnapshot,
    SourceSignalRole,
    inspect_source_candidate,
    resolve_pair_signal_selection,
)
from .persistence import (
    PAIR_SIGNAL_MATERIALIZATION_CLAIM_VERSION,
    SIGNAL_STORE_ENTRY_VERSION,
    PairMaterializationPersistenceConflict,
    PairSignalMaterializationClaim,
    PairSignalSelectionPersistenceDisposition,
    PairSignalSelectionPersistenceResult,
    SignalLineage,
    SignalStorageOrigin,
    SignalStoreEntry,
    SignalStoreIntegrityError,
)


class SQLiteSignalStore:
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
        migration_root = files("fx_signal_store").joinpath("migrations")
        with closing(self._connect()) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            connection.commit()
            for migration in sorted(migration_root.iterdir(), key=lambda item: item.name):
                if not migration.name.endswith(".sql"):
                    continue
                if self._migration_applied(connection, migration.name):
                    continue
                self._apply_migration_exact(
                    connection,
                    migration_name=migration.name,
                    migration_sql=migration.read_text(encoding="utf-8"),
                    applied_at=datetime.now(UTC),
                )

    def _apply_migration_exact(
        self,
        connection: sqlite3.Connection,
        *,
        migration_name: str,
        migration_sql: str,
        applied_at: datetime,
    ) -> None:
        require_utc(applied_at, "migration applied_at")
        connection.execute("BEGIN IMMEDIATE")
        try:
            if self._migration_applied(connection, migration_name):
                connection.commit()
                return
            for statement in self._iter_complete_sql_statements(migration_sql):
                connection.execute(statement)
            self._record_migration(
                connection,
                migration_name=migration_name,
                applied_at=applied_at,
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise

    @staticmethod
    def _migration_applied(
        connection: sqlite3.Connection,
        migration_name: str,
    ) -> bool:
        row = connection.execute(
            "SELECT 1 FROM schema_migrations WHERE version = ?",
            (migration_name,),
        ).fetchone()
        return row is not None

    def _record_migration(
        self,
        connection: sqlite3.Connection,
        *,
        migration_name: str,
        applied_at: datetime,
    ) -> None:
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            (migration_name, applied_at.isoformat()),
        )

    @staticmethod
    def _iter_complete_sql_statements(migration_sql: str) -> Iterable[str]:
        buffer: list[str] = []
        for character in migration_sql:
            buffer.append(character)
            if character != ";":
                continue
            candidate = "".join(buffer)
            if not sqlite3.complete_statement(candidate):
                continue
            statement = candidate.strip()
            if statement:
                yield statement
            buffer.clear()
        if "".join(buffer).strip():
            raise SignalStoreIntegrityError(
                "migration SQL contains an incomplete statement"
            )

    def append_observation(self, observation: NewsObservation) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO observations(
                    id, source, title, body, published_at, first_seen_at, content_hash,
                    payload_reference, normalizer_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    observation.observation_id.value,
                    observation.source,
                    observation.title,
                    observation.body,
                    observation.published_at.isoformat() if observation.published_at else None,
                    observation.first_seen_at.isoformat(),
                    observation.content_hash,
                    observation.payload_reference,
                    observation.normalizer_version,
                ),
            )

    def append_observation_if_absent(self, observation: NewsObservation) -> bool:
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO observations(
                    id, source, title, body, published_at, first_seen_at, content_hash,
                    payload_reference, normalizer_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    observation.observation_id.value,
                    observation.source,
                    observation.title,
                    observation.body,
                    observation.published_at.isoformat() if observation.published_at else None,
                    observation.first_seen_at.isoformat(),
                    observation.content_hash,
                    observation.payload_reference,
                    observation.normalizer_version,
                ),
            )
        return cursor.rowcount == 1

    def append_feature(self, feature: CurrencyFundamentalFeature) -> None:
        factors = [
            {"factor": item.factor.value, "direction": item.direction.value}
            for item in feature.factor_scores
        ]
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO features(
                    id, currency, event_type, factor_scores_json, impact_strength, confidence,
                    producer_version, model_version, prompt_version, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    feature.feature_id.value,
                    feature.currency.code,
                    feature.event_type.value,
                    json.dumps(factors, separators=(",", ":"), sort_keys=True),
                    feature.impact_strength.value,
                    feature.confidence.value,
                    feature.versions.producer_version,
                    feature.versions.model_version,
                    feature.versions.prompt_version,
                    feature.created_at.isoformat(),
                ),
            )
            connection.executemany(
                "INSERT INTO feature_sources(feature_id, observation_id) VALUES (?, ?)",
                ((feature.feature_id.value, item.value) for item in feature.observation_ids),
            )

    def append_feature_if_absent(self, feature: CurrencyFundamentalFeature) -> bool:
        factors = [
            {"factor": item.factor.value, "direction": item.direction.value}
            for item in feature.factor_scores
        ]
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO features(
                    id, currency, event_type, factor_scores_json, impact_strength, confidence,
                    producer_version, model_version, prompt_version, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    feature.feature_id.value,
                    feature.currency.code,
                    feature.event_type.value,
                    json.dumps(factors, separators=(",", ":"), sort_keys=True),
                    feature.impact_strength.value,
                    feature.confidence.value,
                    feature.versions.producer_version,
                    feature.versions.model_version,
                    feature.versions.prompt_version,
                    feature.created_at.isoformat(),
                ),
            )
            if cursor.rowcount == 1:
                connection.executemany(
                    "INSERT INTO feature_sources(feature_id, observation_id) VALUES (?, ?)",
                    ((feature.feature_id.value, item.value) for item in feature.observation_ids),
                )
        return cursor.rowcount == 1

    def append_signal(
        self,
        signal: Signal,
        *,
        stored_at: datetime | None = None,
    ) -> None:
        effective_stored_at = self._effective_stored_at(stored_at)
        with closing(self._connect()) as connection, connection:
            self._append_signal(connection, signal)
            self._append_signal_store_entry(
                connection,
                signal.signal_id,
                effective_stored_at,
                SignalStorageOrigin.APPEND,
            )

    def append_signal_if_absent(
        self,
        signal: Signal,
        *,
        stored_at: datetime | None = None,
    ) -> bool:
        effective_stored_at = self._effective_stored_at(stored_at)
        with closing(self._connect()) as connection, connection:
            inserted = self._append_signal(connection, signal, if_absent=True)
            if inserted:
                self._append_signal_store_entry(
                    connection,
                    signal.signal_id,
                    effective_stored_at,
                    SignalStorageOrigin.APPEND,
                )
            else:
                self._get_signal_store_entry(connection, signal.signal_id)
        return inserted

    def _append_signal(
        self,
        connection: sqlite3.Connection,
        signal: Signal,
        *,
        if_absent: bool = False,
    ) -> bool:
        if isinstance(signal.target, CurrencyTarget):
            target_type = "currency"
            target_value = signal.target.currency.code
        else:
            target_type = "pair"
            target_value = signal.target.pair.symbol
        insert = "INSERT OR IGNORE" if if_absent else "INSERT"
        cursor = connection.execute(
            f"""
            {insert} INTO signals(
                id, target_type, target_value, signal_type, direction, strength, confidence,
                horizon, observed_at, created_at, producer_version, model_version,
                prompt_version, scorer_version, transformation_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                signal.signal_id.value,
                target_type,
                target_value,
                signal.signal_type,
                signal.direction.value,
                signal.strength.value,
                signal.confidence.value,
                signal.horizon.value,
                signal.observed_at.isoformat(),
                signal.created_at.isoformat(),
                signal.versions.producer_version,
                signal.versions.model_version,
                signal.versions.prompt_version,
                signal.versions.scorer_version,
                signal.versions.transformation_version,
            ),
        )
        if cursor.rowcount != 1:
            return False
        connection.executemany(
            "INSERT INTO signal_sources(signal_id, feature_id) VALUES (?, ?)",
            ((signal.signal_id.value, item.value) for item in signal.source_feature_ids),
        )
        return True

    @staticmethod
    def _append_signal_store_entry(
        connection: sqlite3.Connection,
        signal_id: SignalId,
        stored_at: datetime,
        storage_origin: SignalStorageOrigin,
    ) -> None:
        require_utc(stored_at, "Signal Store entry stored_at")
        if not isinstance(storage_origin, SignalStorageOrigin):
            raise TypeError("storage_origin must be SignalStorageOrigin")
        connection.execute(
            """
            INSERT INTO signal_store_entries(
                contract_version, signal_id, stored_at, storage_origin
            ) VALUES (?, ?, ?, ?)
            """,
            (
                SIGNAL_STORE_ENTRY_VERSION,
                signal_id.value,
                stored_at.isoformat(),
                storage_origin.value,
            ),
        )

    @staticmethod
    def _effective_stored_at(stored_at: datetime | None) -> datetime:
        effective = datetime.now(UTC) if stored_at is None else stored_at
        require_utc(effective, "Signal Store entry stored_at")
        return effective

    def get_observation(self, observation_id: ObservationId) -> NewsObservation:
        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT * FROM observations WHERE id = ?", (observation_id.value,)
            ).fetchone()
        if row is None:
            raise KeyError(observation_id.value)
        return NewsObservation(
            observation_id=ObservationId(row["id"]),
            source=row["source"],
            title=row["title"],
            body=row["body"],
            published_at=datetime.fromisoformat(row["published_at"])
            if row["published_at"]
            else None,
            first_seen_at=datetime.fromisoformat(row["first_seen_at"]),
            content_hash=row["content_hash"],
            payload_reference=row["payload_reference"],
            normalizer_version=row["normalizer_version"],
        )

    def get_feature(self, feature_id: FeatureId) -> CurrencyFundamentalFeature:
        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT * FROM features WHERE id = ?", (feature_id.value,)
            ).fetchone()
            sources = connection.execute(
                "SELECT observation_id FROM feature_sources WHERE feature_id = ? "
                "ORDER BY observation_id",
                (feature_id.value,),
            ).fetchall()
        if row is None:
            raise KeyError(feature_id.value)
        factors = tuple(
            FactorScore(
                FundamentalFactor(item["factor"]), DirectionScore(float(item["direction"]))
            )
            for item in json.loads(row["factor_scores_json"])
        )
        return CurrencyFundamentalFeature(
            feature_id=FeatureId(row["id"]),
            observation_ids=tuple(ObservationId(item["observation_id"]) for item in sources),
            currency=Currency(row["currency"]),
            event_type=FundamentalFactor(row["event_type"]),
            factor_scores=factors,
            impact_strength=Probability(float(row["impact_strength"])),
            confidence=Probability(float(row["confidence"])),
            versions=VersionMetadata(
                producer_version=row["producer_version"],
                model_version=row["model_version"],
                prompt_version=row["prompt_version"],
            ),
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def get_signal(self, signal_id: SignalId) -> Signal:
        with closing(self._connect()) as connection, connection:
            return self._get_signal(connection, signal_id)

    def _get_signal(
        self,
        connection: sqlite3.Connection,
        signal_id: SignalId,
    ) -> Signal:
        row = connection.execute(
            "SELECT * FROM signals WHERE id = ?", (signal_id.value,)
        ).fetchone()
        if row is None:
            raise KeyError(signal_id.value)
        sources = connection.execute(
            "SELECT feature_id FROM signal_sources WHERE signal_id = ? ORDER BY feature_id",
            (signal_id.value,),
        ).fetchall()
        return self._signal_from_row(row, sources)

    def list_signals(
        self,
        *,
        target: str | None = None,
        horizon: Horizon | None = None,
        scorer_version: str | None = None,
    ) -> tuple[Signal, ...]:
        clauses: list[str] = []
        parameters: list[str] = []
        if target is not None:
            clauses.append("target_value = ?")
            parameters.append(target)
        if horizon is not None:
            clauses.append("horizon = ?")
            parameters.append(horizon.value)
        if scorer_version is not None:
            clauses.append("scorer_version = ?")
            parameters.append(scorer_version)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with closing(self._connect()) as connection, connection:
            rows = connection.execute(
                f"SELECT id FROM signals{where} ORDER BY created_at, id", parameters
            ).fetchall()
            return tuple(
                self._get_signal(connection, SignalId(row["id"])) for row in rows
            )

    def get_lineage(self, signal_id: SignalId) -> SignalLineage:
        with closing(self._connect()) as connection, connection:
            return self._get_lineage(connection, signal_id)

    @staticmethod
    def _get_lineage(
        connection: sqlite3.Connection,
        signal_id: SignalId,
    ) -> SignalLineage:
        feature_rows = connection.execute(
            "SELECT feature_id FROM signal_sources WHERE signal_id = ? ORDER BY feature_id",
            (signal_id.value,),
        ).fetchall()
        if not feature_rows:
            exists = connection.execute(
                "SELECT 1 FROM signals WHERE id = ?", (signal_id.value,)
            ).fetchone()
            if exists is None:
                raise KeyError(signal_id.value)
        feature_ids = tuple(FeatureId(row["feature_id"]) for row in feature_rows)
        observation_rows = connection.execute(
            """
            SELECT DISTINCT fs.observation_id
            FROM feature_sources fs
            JOIN signal_sources ss ON ss.feature_id = fs.feature_id
            WHERE ss.signal_id = ?
            ORDER BY fs.observation_id
            """,
            (signal_id.value,),
        ).fetchall()
        return SignalLineage(
            signal_id=signal_id,
            feature_ids=feature_ids,
            observation_ids=tuple(ObservationId(row["observation_id"]) for row in observation_rows),
        )

    def _get_signal_snapshot(
        self,
        connection: sqlite3.Connection,
        signal_id: SignalId,
    ) -> SignalContentSnapshot:
        signal = self._get_signal(connection, signal_id)
        lineage = self._get_lineage(connection, signal_id)
        missing_feature = connection.execute(
            """
            SELECT ss.feature_id
            FROM signal_sources AS ss
            LEFT JOIN features AS f ON f.id = ss.feature_id
            WHERE ss.signal_id = ? AND f.id IS NULL
            LIMIT 1
            """,
            (signal_id.value,),
        ).fetchone()
        if missing_feature is not None:
            raise SignalStoreIntegrityError(
                f"Signal {signal_id.value} references an absent Feature"
            )
        missing_observation = connection.execute(
            """
            SELECT fs.observation_id
            FROM signal_sources AS ss
            JOIN feature_sources AS fs ON fs.feature_id = ss.feature_id
            LEFT JOIN observations AS o ON o.id = fs.observation_id
            WHERE ss.signal_id = ? AND o.id IS NULL
            LIMIT 1
            """,
            (signal_id.value,),
        ).fetchone()
        if missing_observation is not None:
            raise SignalStoreIntegrityError(
                f"Signal {signal_id.value} references an absent Observation"
            )
        return SignalContentSnapshot.from_signal(
            signal,
            lineage,
        )

    def get_signal_store_entry(self, signal_id: SignalId) -> SignalStoreEntry:
        with closing(self._connect()) as connection, connection:
            return self._get_signal_store_entry(connection, signal_id)

    def _get_signal_store_entry(
        self,
        connection: sqlite3.Connection,
        signal_id: SignalId,
    ) -> SignalStoreEntry:
        rows = connection.execute(
            "SELECT * FROM signal_store_entries WHERE signal_id = ?",
            (signal_id.value,),
        ).fetchall()
        signal_exists = connection.execute(
            "SELECT 1 FROM signals WHERE id = ?",
            (signal_id.value,),
        ).fetchone()
        if not rows:
            if signal_exists is None:
                raise KeyError(signal_id.value)
            raise SignalStoreIntegrityError(
                f"Signal {signal_id.value} has no Signal Store entry"
            )
        if len(rows) != 1:
            raise SignalStoreIntegrityError(
                f"Signal {signal_id.value} has multiple Signal Store entries"
            )
        if signal_exists is None:
            raise SignalStoreIntegrityError(
                f"Signal Store entry {rows[0]['store_sequence']} has no Signal row"
            )
        return self._signal_store_entry_from_row(rows[0], expected_signal_id=signal_id)

    @staticmethod
    def _signal_store_entry_from_row(
        row: sqlite3.Row,
        *,
        expected_signal_id: SignalId | None = None,
    ) -> SignalStoreEntry:
        try:
            entry = SignalStoreEntry(
                contract_version=row["contract_version"],
                store_sequence=row["store_sequence"],
                signal_id=SignalId(row["signal_id"]),
                stored_at=datetime.fromisoformat(row["stored_at"]),
                storage_origin=SignalStorageOrigin(row["storage_origin"]),
            )
        except (TypeError, ValueError) as error:
            raise SignalStoreIntegrityError(
                "Signal Store catalog contains an invalid Store entry"
            ) from error
        if expected_signal_id is not None and entry.signal_id != expected_signal_id:
            raise SignalStoreIntegrityError(
                "Signal Store entry subject does not match its Signal"
            )
        return entry

    def _validate_signal_store_catalog_integrity(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        missing_entry = connection.execute(
            """
            SELECT s.id
            FROM signals AS s
            LEFT JOIN signal_store_entries AS e ON e.signal_id = s.id
            WHERE e.signal_id IS NULL
            LIMIT 1
            """
        ).fetchone()
        if missing_entry is not None:
            raise SignalStoreIntegrityError(
                "Signal Store catalog contains a Signal without a Store entry"
            )
        orphan_entry = connection.execute(
            """
            SELECT e.signal_id
            FROM signal_store_entries AS e
            LEFT JOIN signals AS s ON s.id = e.signal_id
            WHERE s.id IS NULL
            LIMIT 1
            """
        ).fetchone()
        if orphan_entry is not None:
            raise SignalStoreIntegrityError(
                "Signal Store catalog contains a Store entry without a Signal"
            )
        duplicate_entry = connection.execute(
            """
            SELECT signal_id
            FROM signal_store_entries
            GROUP BY signal_id
            HAVING COUNT(*) != 1
            LIMIT 1
            """
        ).fetchone()
        if duplicate_entry is not None:
            raise SignalStoreIntegrityError(
                "Signal Store catalog contains multiple entries for one Signal"
            )
        rows = connection.execute(
            """
            SELECT e.*, s.id AS subject_signal_id
            FROM signal_store_entries AS e
            JOIN signals AS s ON s.id = e.signal_id
            ORDER BY e.store_sequence
            """
        ).fetchall()
        for row in rows:
            entry = self._signal_store_entry_from_row(row)
            if entry.signal_id.value != row["subject_signal_id"]:
                raise SignalStoreIntegrityError(
                    "Signal Store entry subject does not match its Signal"
                )

    def current_signal_checkpoint(self) -> int:
        with closing(self._connect()) as connection, connection:
            return self._current_signal_checkpoint(connection)

    def _current_signal_checkpoint(self, connection: sqlite3.Connection) -> int:
        self._validate_signal_store_catalog_integrity(connection)
        row = connection.execute(
            "SELECT MAX(store_sequence) AS checkpoint FROM signal_store_entries"
        ).fetchone()
        checkpoint = 0 if row is None or row["checkpoint"] is None else row["checkpoint"]
        if isinstance(checkpoint, bool) or not isinstance(checkpoint, int) or checkpoint < 0:
            raise SignalStoreIntegrityError("Signal Store checkpoint is invalid")
        return checkpoint

    def claim_pair_signal_materialization(
        self,
        request: PairSignalMaterializationRequest,
        *,
        captured_at: datetime,
    ) -> PairSignalMaterializationClaim:
        request.validate_intrinsic_integrity()
        require_utc(captured_at, "materialization claim captured_at")
        if captured_at < request.as_of:
            raise ValueError("materialization claim captured_at cannot be before request as_of")
        with closing(self._connect()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                current_checkpoint = self._current_signal_checkpoint(connection)
                self._append_or_compare_specification(connection, request.specification)
                self._append_or_compare_request(connection, request)
                existing = self._get_materialization_claim(connection, request.request_id)
                if existing is not None:
                    self._validate_materialization_claim_checkpoint(
                        connection,
                        existing,
                        current_checkpoint=current_checkpoint,
                    )
                    connection.commit()
                    return existing
                connection.execute(
                    """
                    INSERT INTO pair_signal_materialization_claims(
                        request_id, contract_version, checkpoint_sequence, captured_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        request.request_id,
                        PAIR_SIGNAL_MATERIALIZATION_CLAIM_VERSION,
                        current_checkpoint,
                        captured_at.isoformat(),
                    ),
                )
                inserted = self._get_materialization_claim(connection, request.request_id)
                if inserted is None:
                    raise SignalStoreIntegrityError(
                        "materialization Claim insert produced no persisted row"
                    )
                self._validate_materialization_claim_checkpoint(
                    connection,
                    inserted,
                    current_checkpoint=current_checkpoint,
                )
                connection.commit()
                return inserted
            except Exception:
                connection.rollback()
                raise

    @staticmethod
    def _validate_materialization_claim_checkpoint(
        connection: sqlite3.Connection,
        claim: PairSignalMaterializationClaim,
        *,
        current_checkpoint: int,
    ) -> None:
        if claim.checkpoint_sequence > current_checkpoint:
            raise SignalStoreIntegrityError(
                "materialization Claim checkpoint exceeds current checkpoint"
            )
        if claim.checkpoint_sequence == 0:
            return
        row = connection.execute(
            "SELECT COUNT(*) FROM signal_store_entries WHERE store_sequence = ?",
            (claim.checkpoint_sequence,),
        ).fetchone()
        if row is None or row[0] != 1:
            raise SignalStoreIntegrityError(
                "materialization Claim checkpoint does not reference a Store entry"
            )

    def capture_pair_signal_selection(
        self,
        request: PairSignalMaterializationRequest,
    ) -> PairSignalSelectionPersistenceResult:
        request.validate_intrinsic_integrity()
        with closing(self._connect()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                current_checkpoint = self._current_signal_checkpoint(connection)
                claim = self._get_exact_materialization_claim(
                    connection,
                    request,
                    current_checkpoint=current_checkpoint,
                )
                candidates = self._capture_pair_signal_candidates(connection, claim)
                expected = resolve_pair_signal_selection(
                    claim.request,
                    claim.checkpoint_sequence,
                    claim.captured_at,
                    candidates,
                )
                existing = self._get_selection_snapshot(
                    connection,
                    request_id=request.request_id,
                    expected_selection_snapshot_id=expected.selection_snapshot_id,
                )
                if existing is not None:
                    self._validate_selection_snapshot_relational_integrity(
                        connection,
                        existing,
                        claim,
                    )
                    if existing != expected:
                        raise SignalStoreIntegrityError(
                            "persisted selection evidence differs from its source Store"
                        )
                    connection.commit()
                    return PairSignalSelectionPersistenceResult(
                        disposition=(
                            PairSignalSelectionPersistenceDisposition.REUSED_IDENTICAL
                        ),
                        selection_snapshot=existing,
                    )

                self._append_selection_snapshot_exact(connection, expected)
                persisted = self._get_selection_snapshot(
                    connection,
                    request_id=request.request_id,
                    expected_selection_snapshot_id=expected.selection_snapshot_id,
                )
                if persisted is None:
                    raise SignalStoreIntegrityError(
                        "selection evidence insert produced no persisted Snapshot"
                    )
                self._validate_selection_snapshot_relational_integrity(
                    connection,
                    persisted,
                    claim,
                )
                if persisted != expected:
                    raise SignalStoreIntegrityError(
                        "selection evidence did not round-trip exactly"
                    )
                connection.commit()
                return PairSignalSelectionPersistenceResult(
                    disposition=PairSignalSelectionPersistenceDisposition.INSERTED,
                    selection_snapshot=persisted,
                )
            except Exception:
                connection.rollback()
                raise

    def _get_exact_materialization_claim(
        self,
        connection: sqlite3.Connection,
        request: PairSignalMaterializationRequest,
        *,
        current_checkpoint: int,
    ) -> PairSignalMaterializationClaim:
        persisted_request = self._get_request(connection, request.request_id)
        if persisted_request is None:
            raise PairMaterializationPersistenceConflict(
                "materialization Request must be persisted before selection capture"
            )
        if persisted_request != request:
            raise PairMaterializationPersistenceConflict(
                "supplied materialization Request differs from persisted content"
            )
        claim = self._get_materialization_claim(connection, request.request_id)
        if claim is None:
            raise PairMaterializationPersistenceConflict(
                "materialization Claim must exist before selection capture"
            )
        self._validate_materialization_claim_checkpoint(
            connection,
            claim,
            current_checkpoint=current_checkpoint,
        )
        return claim

    def _capture_pair_signal_candidates(
        self,
        connection: sqlite3.Connection,
        claim: PairSignalMaterializationClaim,
    ) -> tuple[PairSignalSelectionCandidate, ...]:
        candidates: list[PairSignalSelectionCandidate] = []
        for entry in self._list_signal_store_entries_through_checkpoint(
            connection,
            claim.checkpoint_sequence,
        ):
            try:
                snapshot = self._get_signal_snapshot(connection, entry.signal_id)
                for role in (SourceSignalRole.BASE, SourceSignalRole.QUOTE):
                    candidates.append(
                        inspect_source_candidate(
                            claim.request,
                            role,
                            snapshot,
                            entry.store_sequence,
                        )
                    )
            except (KeyError, TypeError, ValueError) as error:
                raise SignalStoreIntegrityError(
                    f"Signal {entry.signal_id.value} cannot be captured as selection evidence"
                ) from error
        return tuple(candidates)

    def _list_signal_store_entries_through_checkpoint(
        self,
        connection: sqlite3.Connection,
        checkpoint_sequence: int,
    ) -> tuple[SignalStoreEntry, ...]:
        rows = connection.execute(
            "SELECT * FROM signal_store_entries "
            "WHERE store_sequence <= ? ORDER BY store_sequence",
            (checkpoint_sequence,),
        ).fetchall()
        return tuple(self._signal_store_entry_from_row(row) for row in rows)

    @staticmethod
    def _append_selection_snapshot_exact(
        connection: sqlite3.Connection,
        snapshot: PairSignalSelectionSnapshot,
    ) -> None:
        snapshot.validate_intrinsic_integrity()
        connection.execute(
            """
            INSERT INTO pair_signal_selection_snapshots(
                selection_snapshot_id, contract_version, request_id,
                checkpoint_sequence, captured_at, candidate_set_hash, outcome,
                reason, selected_base_candidate_id, selected_quote_candidate_id,
                selected_base_signal_id, selected_quote_signal_id,
                selected_observation_group_identity
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot.selection_snapshot_id,
                snapshot.contract_version,
                snapshot.request_id,
                snapshot.checkpoint_sequence,
                snapshot.captured_at.isoformat(),
                snapshot.candidate_set_hash,
                snapshot.outcome.value,
                snapshot.reason.value,
                snapshot.selected_base_candidate_id,
                snapshot.selected_quote_candidate_id,
                None
                if snapshot.selected_base_signal_id is None
                else snapshot.selected_base_signal_id.value,
                None
                if snapshot.selected_quote_signal_id is None
                else snapshot.selected_quote_signal_id.value,
                snapshot.selected_observation_group_identity,
            ),
        )
        for candidate_ordinal, candidate in enumerate(snapshot.candidates):
            connection.execute(
                """
                INSERT INTO pair_signal_selection_candidates(
                    candidate_id, selection_snapshot_id, candidate_ordinal,
                    contract_version, request_id, role, signal_id,
                    signal_content_hash, store_sequence,
                    observation_group_identity, eligibility, rejection_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate.candidate_id,
                    snapshot.selection_snapshot_id,
                    candidate_ordinal,
                    candidate.contract_version,
                    candidate.request_id,
                    candidate.role.value,
                    candidate.signal_snapshot.signal_id.value,
                    candidate.signal_snapshot.signal_content_hash,
                    candidate.store_sequence,
                    candidate.observation_group_identity,
                    candidate.eligibility.value,
                    None
                    if candidate.rejection_reason is None
                    else candidate.rejection_reason.value,
                ),
            )
            connection.executemany(
                """
                INSERT INTO pair_signal_selection_candidate_observations(
                    candidate_id, observation_ordinal, observation_id
                ) VALUES (?, ?, ?)
                """,
                (
                    (candidate.candidate_id, ordinal, observation_id.value)
                    for ordinal, observation_id in enumerate(candidate.observation_ids)
                ),
            )

    def _get_selection_snapshot(
        self,
        connection: sqlite3.Connection,
        *,
        request_id: str,
        expected_selection_snapshot_id: str,
    ) -> PairSignalSelectionSnapshot | None:
        rows = connection.execute(
            """
            SELECT * FROM pair_signal_selection_snapshots
            WHERE request_id = ? OR selection_snapshot_id = ?
            """,
            (request_id, expected_selection_snapshot_id),
        ).fetchall()
        if not rows:
            return None
        if len(rows) != 1:
            raise SignalStoreIntegrityError(
                "one materialization Request maps to conflicting selection Snapshots"
            )
        row = rows[0]
        request = self._get_request(connection, row["request_id"])
        if request is None:
            raise SignalStoreIntegrityError(
                "selection Snapshot has no materialization Request"
            )
        candidates = self._hydrate_selection_candidates(
            connection,
            selection_snapshot_id=row["selection_snapshot_id"],
            request=request,
        )
        try:
            snapshot = PairSignalSelectionSnapshot(
                selection_snapshot_id=row["selection_snapshot_id"],
                contract_version=row["contract_version"],
                request=request,
                checkpoint_sequence=row["checkpoint_sequence"],
                captured_at=datetime.fromisoformat(row["captured_at"]),
                candidates=candidates,
                candidate_set_hash=row["candidate_set_hash"],
                outcome=PairSignalSelectionOutcome(row["outcome"]),
                reason=PairSignalSelectionReason(row["reason"]),
                selected_base_candidate_id=row["selected_base_candidate_id"],
                selected_quote_candidate_id=row["selected_quote_candidate_id"],
                selected_base_signal_id=(
                    None
                    if row["selected_base_signal_id"] is None
                    else SignalId(row["selected_base_signal_id"])
                ),
                selected_quote_signal_id=(
                    None
                    if row["selected_quote_signal_id"] is None
                    else SignalId(row["selected_quote_signal_id"])
                ),
                selected_observation_group_identity=(
                    row["selected_observation_group_identity"]
                ),
            )
        except (TypeError, ValueError) as error:
            raise SignalStoreIntegrityError(
                "persisted Pair Signal selection Snapshot is invalid"
            ) from error
        return snapshot

    def _hydrate_selection_candidates(
        self,
        connection: sqlite3.Connection,
        *,
        selection_snapshot_id: str,
        request: PairSignalMaterializationRequest,
    ) -> tuple[PairSignalSelectionCandidate, ...]:
        rows = connection.execute(
            """
            SELECT * FROM pair_signal_selection_candidates
            WHERE selection_snapshot_id = ? ORDER BY candidate_ordinal
            """,
            (selection_snapshot_id,),
        ).fetchall()
        ordinals = tuple(row["candidate_ordinal"] for row in rows)
        if ordinals != tuple(range(len(rows))):
            raise SignalStoreIntegrityError(
                "selection candidate ordinals must be contiguous and canonical"
            )
        candidates: list[PairSignalSelectionCandidate] = []
        for row in rows:
            try:
                signal_id = SignalId(row["signal_id"])
                role = SourceSignalRole(row["role"])
                persisted_eligibility = PairSignalCandidateEligibility(
                    row["eligibility"]
                )
                persisted_reason = (
                    None
                    if row["rejection_reason"] is None
                    else PairSignalCandidateRejectionReason(row["rejection_reason"])
                )
            except (TypeError, ValueError) as error:
                raise SignalStoreIntegrityError(
                    "persisted Pair Signal selection candidate is invalid"
                ) from error
            try:
                entry = self._get_signal_store_entry(connection, signal_id)
            except KeyError as error:
                raise SignalStoreIntegrityError(
                    "selection candidate references an absent source Signal"
                ) from error
            if entry.store_sequence != row["store_sequence"]:
                raise SignalStoreIntegrityError(
                    "selection candidate does not reference its exact Store entry"
                )
            try:
                signal_snapshot = self._get_signal_snapshot(connection, signal_id)
                expected = inspect_source_candidate(
                    request,
                    role,
                    signal_snapshot,
                    entry.store_sequence,
                )
            except (KeyError, TypeError, ValueError) as error:
                raise SignalStoreIntegrityError(
                    "selection candidate source Signal cannot be reconstructed"
                ) from error
            observation_rows = connection.execute(
                """
                SELECT observation_ordinal, observation_id
                FROM pair_signal_selection_candidate_observations
                WHERE candidate_id = ? ORDER BY observation_ordinal
                """,
                (row["candidate_id"],),
            ).fetchall()
            observation_ordinals = tuple(
                item["observation_ordinal"] for item in observation_rows
            )
            if observation_ordinals != tuple(range(len(observation_rows))):
                raise SignalStoreIntegrityError(
                    "candidate Observation ordinals must be contiguous and canonical"
                )
            try:
                persisted_observations = tuple(
                    ObservationId(item["observation_id"])
                    for item in observation_rows
                )
            except (TypeError, ValueError) as error:
                raise SignalStoreIntegrityError(
                    "persisted candidate Observation lineage is invalid"
                ) from error
            persisted_values = (
                row["candidate_id"],
                row["contract_version"],
                row["request_id"],
                role,
                signal_id,
                row["signal_content_hash"],
                row["store_sequence"],
                row["observation_group_identity"],
                persisted_observations,
                persisted_eligibility,
                persisted_reason,
            )
            expected_values = (
                expected.candidate_id,
                expected.contract_version,
                expected.request_id,
                expected.role,
                expected.signal_snapshot.signal_id,
                expected.signal_snapshot.signal_content_hash,
                expected.store_sequence,
                expected.observation_group_identity,
                expected.observation_ids,
                expected.eligibility,
                expected.rejection_reason,
            )
            if persisted_values != expected_values:
                raise SignalStoreIntegrityError(
                    "persisted selection candidate differs from reconstructed evidence"
                )
            candidates.append(expected)
        return tuple(candidates)

    def _validate_selection_snapshot_relational_integrity(
        self,
        connection: sqlite3.Connection,
        snapshot: PairSignalSelectionSnapshot,
        claim: PairSignalMaterializationClaim,
    ) -> None:
        snapshot.validate_intrinsic_integrity()
        claim.validate_intrinsic_integrity()
        persisted_claim = self._get_materialization_claim(
            connection,
            claim.request.request_id,
        )
        if persisted_claim != claim:
            raise SignalStoreIntegrityError(
                "selection Snapshot authority differs from the persisted Claim"
            )
        if snapshot.request != claim.request:
            raise SignalStoreIntegrityError(
                "selection Snapshot does not belong to its materialization Claim"
            )
        if snapshot.checkpoint_sequence != claim.checkpoint_sequence:
            raise SignalStoreIntegrityError(
                "selection Snapshot checkpoint differs from its Claim"
            )
        if snapshot.captured_at != claim.captured_at:
            raise SignalStoreIntegrityError(
                "selection Snapshot captured_at differs from its Claim"
            )
        expected = resolve_pair_signal_selection(
            claim.request,
            claim.checkpoint_sequence,
            claim.captured_at,
            snapshot.candidates,
        )
        if snapshot != expected:
            raise SignalStoreIntegrityError(
                "selection Snapshot differs from the shared resolver result"
            )

    def _append_or_compare_specification(
        self,
        connection: sqlite3.Connection,
        specification: PairSignalMaterializationSpecification,
    ) -> PairSignalMaterializationSpecification:
        specification.validate_intrinsic_integrity()
        existing = self._get_specification(connection, specification.specification_id)
        if existing is not None:
            if existing != specification:
                raise PairMaterializationPersistenceConflict(
                    "materialization specification content conflicts with persisted row"
                )
            return existing
        connection.execute(
            """
            INSERT INTO pair_signal_materialization_specifications(
                specification_id, contract_version, pair, source_signal_type,
                output_signal_type, horizon, producer_version, model_version,
                prompt_version, scorer_version,
                expected_source_transformation_version, output_transformation_version,
                source_signal_max_age_microseconds, observation_group_policy_version,
                selection_policy_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                specification.specification_id,
                specification.contract_version,
                specification.pair.symbol,
                specification.source_signal_type,
                specification.output_signal_type,
                specification.horizon.value,
                specification.producer_version,
                specification.model_version,
                specification.prompt_version,
                specification.scorer_version,
                specification.expected_source_transformation_version,
                specification.output_transformation_version,
                self._timedelta_microseconds(specification.source_signal_max_age),
                specification.observation_group_policy_version,
                specification.selection_policy_version,
            ),
        )
        persisted = self._get_specification(connection, specification.specification_id)
        if persisted != specification:
            raise SignalStoreIntegrityError(
                "materialization specification did not round-trip exactly"
            )
        return persisted

    @staticmethod
    def _get_specification(
        connection: sqlite3.Connection,
        specification_id: str,
    ) -> PairSignalMaterializationSpecification | None:
        row = connection.execute(
            """
            SELECT * FROM pair_signal_materialization_specifications
            WHERE specification_id = ?
            """,
            (specification_id,),
        ).fetchone()
        if row is None:
            return None
        try:
            return PairSignalMaterializationSpecification(
                specification_id=row["specification_id"],
                contract_version=row["contract_version"],
                pair=CurrencyPair.parse(row["pair"]),
                source_signal_type=row["source_signal_type"],
                output_signal_type=row["output_signal_type"],
                horizon=Horizon(row["horizon"]),
                producer_version=row["producer_version"],
                model_version=row["model_version"],
                prompt_version=row["prompt_version"],
                scorer_version=row["scorer_version"],
                expected_source_transformation_version=(
                    row["expected_source_transformation_version"]
                ),
                output_transformation_version=row["output_transformation_version"],
                source_signal_max_age=timedelta(
                    microseconds=row["source_signal_max_age_microseconds"]
                ),
                observation_group_policy_version=(
                    row["observation_group_policy_version"]
                ),
                selection_policy_version=row["selection_policy_version"],
            )
        except (OverflowError, TypeError, ValueError) as error:
            raise SignalStoreIntegrityError(
                f"materialization specification {specification_id} is invalid"
            ) from error

    def _append_or_compare_request(
        self,
        connection: sqlite3.Connection,
        request: PairSignalMaterializationRequest,
    ) -> PairSignalMaterializationRequest:
        request.validate_intrinsic_integrity()
        existing = self._get_request(connection, request.request_id)
        if existing is not None:
            if existing != request:
                raise PairMaterializationPersistenceConflict(
                    "materialization request content conflicts with persisted row"
                )
            return existing
        business_key_row = connection.execute(
            """
            SELECT request_id FROM pair_signal_materialization_requests
            WHERE pair = ? AND as_of = ? AND specification_id = ?
            """,
            (
                request.pair.symbol,
                request.as_of.isoformat(),
                request.specification.specification_id,
            ),
        ).fetchone()
        if business_key_row is not None:
            raise PairMaterializationPersistenceConflict(
                "Pair/as-of/specification already maps to another request"
            )
        connection.execute(
            """
            INSERT INTO pair_signal_materialization_requests(
                request_id, contract_version, pair, as_of, specification_id
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                request.request_id,
                request.contract_version,
                request.pair.symbol,
                request.as_of.isoformat(),
                request.specification.specification_id,
            ),
        )
        persisted = self._get_request(connection, request.request_id)
        if persisted != request:
            raise SignalStoreIntegrityError("materialization request did not round-trip exactly")
        return persisted

    def _get_request(
        self,
        connection: sqlite3.Connection,
        request_id: str,
    ) -> PairSignalMaterializationRequest | None:
        row = connection.execute(
            "SELECT * FROM pair_signal_materialization_requests WHERE request_id = ?",
            (request_id,),
        ).fetchone()
        if row is None:
            return None
        specification = self._get_specification(connection, row["specification_id"])
        if specification is None:
            raise SignalStoreIntegrityError(
                f"materialization request {request_id} has no specification"
            )
        try:
            return PairSignalMaterializationRequest(
                request_id=row["request_id"],
                contract_version=row["contract_version"],
                pair=CurrencyPair.parse(row["pair"]),
                as_of=datetime.fromisoformat(row["as_of"]),
                specification=specification,
            )
        except (TypeError, ValueError) as error:
            raise SignalStoreIntegrityError(
                f"materialization request {request_id} is invalid"
            ) from error

    def _get_materialization_claim(
        self,
        connection: sqlite3.Connection,
        request_id: str,
    ) -> PairSignalMaterializationClaim | None:
        row = connection.execute(
            "SELECT * FROM pair_signal_materialization_claims WHERE request_id = ?",
            (request_id,),
        ).fetchone()
        if row is None:
            return None
        request = self._get_request(connection, request_id)
        if request is None:
            raise SignalStoreIntegrityError(
                f"materialization Claim {request_id} has no Request"
            )
        try:
            return PairSignalMaterializationClaim(
                contract_version=row["contract_version"],
                request=request,
                checkpoint_sequence=row["checkpoint_sequence"],
                captured_at=datetime.fromisoformat(row["captured_at"]),
            )
        except (TypeError, ValueError) as error:
            raise SignalStoreIntegrityError(
                f"materialization Claim {request_id} is invalid"
            ) from error

    @staticmethod
    def _timedelta_microseconds(value: timedelta) -> int:
        return (
            value.days * 86_400_000_000
            + value.seconds * 1_000_000
            + value.microseconds
        )

    @staticmethod
    def _signal_from_row(row: sqlite3.Row, sources: Iterable[sqlite3.Row]) -> Signal:
        target = (
            CurrencyTarget(Currency(row["target_value"]))
            if row["target_type"] == "currency"
            else PairTarget(CurrencyPair.parse(row["target_value"]))
        )
        direction = (
            DirectionScore(float(row["direction"]))
            if row["target_type"] == "currency"
            else PairScore(float(row["direction"]))
        )
        return Signal(
            signal_id=SignalId(row["id"]),
            target=target,
            signal_type=row["signal_type"],
            direction=direction,
            strength=Probability(float(row["strength"])),
            confidence=Probability(float(row["confidence"])),
            horizon=Horizon(row["horizon"]),
            observed_at=datetime.fromisoformat(row["observed_at"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            source_feature_ids=tuple(FeatureId(item["feature_id"]) for item in sources),
            versions=VersionMetadata(
                producer_version=row["producer_version"],
                model_version=row["model_version"],
                prompt_version=row["prompt_version"],
                scorer_version=row["scorer_version"],
                transformation_version=row["transformation_version"],
            ),
        )
