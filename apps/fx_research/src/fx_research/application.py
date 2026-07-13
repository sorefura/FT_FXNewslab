import hashlib
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime

from fx_core import FeatureId, FundamentalSignalScorer, SignalId
from fx_core.time import require_utc
from fx_signal_store import SQLiteSignalStore

from .collection import NewsSource
from .errors import FeatureProductionError
from .feature_production import VersionedLlmFeatureExtractor
from .normalization import NewsNormalizer
from .persistence import CollectionStage, SQLiteIngestionStateStore


@dataclass(frozen=True, slots=True)
class CollectOnceResult:
    source_id: str
    fetched: int
    inserted: int
    duplicates: int


@dataclass(frozen=True, slots=True)
class ProduceSignalsOnceResult:
    attempted: int
    completed: int
    failed: int


class CollectOnceService:
    def __init__(
        self,
        signal_store: SQLiteSignalStore,
        state_store: SQLiteIngestionStateStore,
        normalizer: NewsNormalizer | None = None,
    ) -> None:
        self._signal_store = signal_store
        self._state_store = state_store
        self._normalizer = normalizer or NewsNormalizer()

    def run(self, source: NewsSource, *, fetched_at: datetime) -> CollectOnceResult:
        require_utc(fetched_at, "fetched_at")
        try:
            items = source.fetch()
        except Exception as error:
            self._state_store.record_fetch(
                source_id=source.source_id,
                fetched_at=fetched_at,
                status="FAILED",
                stage=CollectionStage.RETRIEVAL,
                item_count=0,
                processed_item_count=0,
                error=error,
            )
            raise
        inserted = 0
        processed = 0
        for item in items:
            try:
                observation = self._normalizer.normalize(
                    item, first_seen_at=fetched_at
                )
            except Exception as error:
                self._record_collection_failure(
                    source_id=source.source_id,
                    fetched_at=fetched_at,
                    stage=CollectionStage.NORMALIZATION,
                    item_count=len(items),
                    processed_item_count=processed,
                    error=error,
                )
                raise
            try:
                first_seen_at = self._state_store.first_seen_at(
                    observation_id=observation.observation_id,
                    source_id=item.source_id,
                    currency=item.candidate_currency,
                    canonical_url=observation.payload_reference,
                    content_hash=observation.content_hash,
                    source_date_text=item.source_date_text,
                    recognized_at=fetched_at,
                )
                if first_seen_at != observation.first_seen_at:
                    observation = replace(observation, first_seen_at=first_seen_at)
                inserted += int(
                    self._signal_store.append_observation_if_absent(observation)
                )
            except Exception as error:
                self._record_collection_failure(
                    source_id=source.source_id,
                    fetched_at=fetched_at,
                    stage=CollectionStage.PERSISTENCE,
                    item_count=len(items),
                    processed_item_count=processed,
                    error=error,
                )
                raise
            processed += 1
        self._state_store.record_fetch(
            source_id=source.source_id,
            fetched_at=fetched_at,
            status="SUCCESS",
            stage=CollectionStage.COMPLETED,
            item_count=len(items),
            processed_item_count=processed,
        )
        return CollectOnceResult(
            source_id=source.source_id,
            fetched=len(items),
            inserted=inserted,
            duplicates=len(items) - inserted,
        )

    def _record_collection_failure(
        self,
        *,
        source_id: str,
        fetched_at: datetime,
        stage: CollectionStage,
        item_count: int,
        processed_item_count: int,
        error: Exception,
    ) -> None:
        self._state_store.record_fetch(
            source_id=source_id,
            fetched_at=fetched_at,
            status="FAILED",
            stage=stage,
            item_count=item_count,
            processed_item_count=processed_item_count,
            error=error,
        )


class ProduceSignalsOnceService:
    def __init__(
        self,
        signal_store: SQLiteSignalStore,
        state_store: SQLiteIngestionStateStore,
        *,
        clock: Callable[[], datetime],
    ) -> None:
        self._signal_store = signal_store
        self._state_store = state_store
        self._clock = clock
        self._scorer = FundamentalSignalScorer()

    def run(self, extractor: VersionedLlmFeatureExtractor) -> ProduceSignalsOnceResult:
        versions = extractor.versions
        producer_version = versions.producer_version or ""
        model_version = versions.model_version or ""
        prompt_version = versions.prompt_version or ""
        pending = self._state_store.pending_items(
            producer_version=producer_version,
            model_version=model_version,
            prompt_version=prompt_version,
        )
        completed = 0
        failed = 0
        for item in pending:
            observation = self._signal_store.get_observation(item.observation_id)
            latest_timestamp = observation.first_seen_at
            feature_id = FeatureId(
                "feature-"
                + self._digest(
                    item.observation_id.value,
                    producer_version,
                    model_version,
                    prompt_version,
                )
            )
            signal_id = SignalId(
                "signal-" + self._digest(feature_id.value, self._scorer.scorer_version)
            )
            try:
                try:
                    feature = self._signal_store.get_feature(feature_id)
                except KeyError:
                    extracted = extractor.extract(
                        observation, feature_id=feature_id, currency=item.currency
                    )
                    if self._signal_store.append_feature_if_absent(extracted):
                        feature = extracted
                    else:
                        feature = self._signal_store.get_feature(feature_id)
                if feature.created_at < observation.first_seen_at:
                    raise FeatureProductionError(
                        "Feature cannot be created before its source Observation was first seen"
                    )
                latest_timestamp = feature.created_at
                signal_created_at = self._clock_at_or_after(feature.created_at)
                signal = self._scorer.score(
                    feature,
                    signal_id=signal_id,
                    observed_at=observation.first_seen_at,
                    created_at=signal_created_at,
                )
                if self._signal_store.append_signal_if_absent(signal):
                    persisted_signal = signal
                else:
                    persisted_signal = self._signal_store.get_signal(signal_id)
                if persisted_signal.created_at < feature.created_at:
                    raise FeatureProductionError(
                        "Signal cannot predate its source Feature"
                    )
                latest_timestamp = persisted_signal.created_at
                production_updated_at = self._clock_at_or_after(
                    persisted_signal.created_at
                )
                self._state_store.record_production(
                    observation_id=item.observation_id,
                    producer_version=producer_version,
                    model_version=model_version,
                    prompt_version=prompt_version,
                    status="COMPLETED",
                    updated_at=production_updated_at,
                    feature_id=feature_id.value,
                    signal_id=signal_id.value,
                )
                completed += 1
            except Exception as error:
                failed_at = self._clock_at_or_after(latest_timestamp)
                self._state_store.record_production(
                    observation_id=item.observation_id,
                    producer_version=producer_version,
                    model_version=model_version,
                    prompt_version=prompt_version,
                    status="FAILED",
                    updated_at=failed_at,
                    error=error,
                )
                failed += 1
        return ProduceSignalsOnceResult(len(pending), completed, failed)

    def _clock_at_or_after(self, timestamp: datetime) -> datetime:
        current = self._clock()
        require_utc(current, "production clock")
        return max(current, timestamp)

    @staticmethod
    def _digest(*parts: str) -> str:
        return hashlib.sha256("\0".join(parts).encode()).hexdigest()
