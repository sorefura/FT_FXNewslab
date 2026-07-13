import json
import sqlite3
from collections.abc import Iterable
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
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


@dataclass(frozen=True, slots=True)
class SignalLineage:
    signal_id: SignalId
    feature_ids: tuple[FeatureId, ...]
    observation_ids: tuple[ObservationId, ...]


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

    def append_signal(self, signal: Signal) -> None:
        if isinstance(signal.target, CurrencyTarget):
            target_type = "currency"
            target_value = signal.target.currency.code
        else:
            target_type = "pair"
            target_value = signal.target.pair.symbol
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO signals(
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
            connection.executemany(
                "INSERT INTO signal_sources(signal_id, feature_id) VALUES (?, ?)",
                ((signal.signal_id.value, item.value) for item in signal.source_feature_ids),
            )

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
            row = connection.execute(
                "SELECT * FROM signals WHERE id = ?", (signal_id.value,)
            ).fetchone()
            sources = connection.execute(
                "SELECT feature_id FROM signal_sources WHERE signal_id = ? ORDER BY feature_id",
                (signal_id.value,),
            ).fetchall()
        if row is None:
            raise KeyError(signal_id.value)
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
        return tuple(self.get_signal(SignalId(row["id"])) for row in rows)

    def get_lineage(self, signal_id: SignalId) -> SignalLineage:
        with closing(self._connect()) as connection, connection:
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
