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
    PairSignalMaterializationRequest,
    PairSignalMaterializationSpecification,
    SignalContentSnapshot,
)
from .persistence import (
    PAIR_SIGNAL_MATERIALIZATION_CLAIM_VERSION,
    SIGNAL_STORE_ENTRY_VERSION,
    PairMaterializationPersistenceConflict,
    PairSignalMaterializationClaim,
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
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            applied = {
                row[0] for row in connection.execute("SELECT version FROM schema_migrations")
            }
            for migration in sorted(migration_root.iterdir(), key=lambda item: item.name):
                if not migration.name.endswith(".sql") or migration.name in applied:
                    continue
                connection.executescript(migration.read_text(encoding="utf-8"))
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (migration.name, datetime.now(UTC).isoformat()),
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
        return SignalContentSnapshot.from_signal(
            self._get_signal(connection, signal_id),
            self._get_lineage(connection, signal_id),
        )

    def get_signal_store_entry(self, signal_id: SignalId) -> SignalStoreEntry:
        with closing(self._connect()) as connection, connection:
            return self._get_signal_store_entry(connection, signal_id)

    def _get_signal_store_entry(
        self,
        connection: sqlite3.Connection,
        signal_id: SignalId,
    ) -> SignalStoreEntry:
        row = connection.execute(
            "SELECT * FROM signal_store_entries WHERE signal_id = ?",
            (signal_id.value,),
        ).fetchone()
        signal_exists = connection.execute(
            "SELECT 1 FROM signals WHERE id = ?",
            (signal_id.value,),
        ).fetchone()
        if row is None:
            if signal_exists is None:
                raise KeyError(signal_id.value)
            raise SignalStoreIntegrityError(
                f"Signal {signal_id.value} has no Signal Store entry"
            )
        if signal_exists is None:
            raise SignalStoreIntegrityError(
                f"Signal Store entry {row['store_sequence']} has no Signal row"
            )
        try:
            return SignalStoreEntry(
                contract_version=row["contract_version"],
                store_sequence=row["store_sequence"],
                signal_id=SignalId(row["signal_id"]),
                stored_at=datetime.fromisoformat(row["stored_at"]),
                storage_origin=SignalStorageOrigin(row["storage_origin"]),
            )
        except (TypeError, ValueError) as error:
            raise SignalStoreIntegrityError(
                f"Signal Store entry for {signal_id.value} is invalid"
            ) from error

    def current_signal_checkpoint(self) -> int:
        with closing(self._connect()) as connection, connection:
            return self._current_signal_checkpoint(connection)

    @staticmethod
    def _current_signal_checkpoint(connection: sqlite3.Connection) -> int:
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
                self._append_or_compare_specification(connection, request.specification)
                self._append_or_compare_request(connection, request)
                existing = self._get_materialization_claim(connection, request.request_id)
                if existing is not None:
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
                        self._current_signal_checkpoint(connection),
                        captured_at.isoformat(),
                    ),
                )
                inserted = self._get_materialization_claim(connection, request.request_id)
                if inserted is None:
                    raise SignalStoreIntegrityError(
                        "materialization Claim insert produced no persisted row"
                    )
                connection.commit()
                return inserted
            except Exception:
                connection.rollback()
                raise

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
