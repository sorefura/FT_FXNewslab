import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from fx_core import FeatureId, FundamentalSignalScorer, SignalId
from fx_core.time import require_utc
from fx_signal_store import SQLiteSignalStore

from .collection import NewsSource
from .feature_production import VersionedLlmFeatureExtractor
from .normalization import NewsNormalizer
from .persistence import SQLiteIngestionStateStore


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
                item_count=0,
                error=error,
            )
            raise
        inserted = 0
        for item in items:
            observation_id = self._normalizer.observation_id(item)
            title = self._normalizer.normalize_text(item.title)
            body = self._normalizer.normalize_text(item.body)
            content_hash = self._normalizer.content_hash(title, body)
            canonical_url = self._normalizer.canonical_url(item.canonical_url)
            first_seen_at = self._state_store.first_seen_at(
                observation_id=observation_id,
                source_id=item.source_id,
                currency=item.candidate_currency,
                canonical_url=canonical_url,
                content_hash=content_hash,
                source_date_text=item.source_date_text,
                recognized_at=fetched_at,
            )
            observation = self._normalizer.normalize(item, first_seen_at=first_seen_at)
            inserted += int(self._signal_store.append_observation_if_absent(observation))
        self._state_store.record_fetch(
            source_id=source.source_id,
            fetched_at=fetched_at,
            status="SUCCESS",
            item_count=len(items),
        )
        return CollectOnceResult(
            source_id=source.source_id,
            fetched=len(items),
            inserted=inserted,
            duplicates=len(items) - inserted,
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
            now = self._clock()
            observation = self._signal_store.get_observation(item.observation_id)
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
                signal = self._scorer.score(
                    feature,
                    signal_id=signal_id,
                    observed_at=observation.first_seen_at,
                    created_at=now,
                )
                self._signal_store.append_signal_if_absent(signal)
                self._state_store.record_production(
                    observation_id=item.observation_id,
                    producer_version=producer_version,
                    model_version=model_version,
                    prompt_version=prompt_version,
                    status="COMPLETED",
                    updated_at=now,
                    feature_id=feature_id.value,
                    signal_id=signal_id.value,
                )
                completed += 1
            except Exception as error:
                self._state_store.record_production(
                    observation_id=item.observation_id,
                    producer_version=producer_version,
                    model_version=model_version,
                    prompt_version=prompt_version,
                    status="FAILED",
                    updated_at=now,
                    error=error,
                )
                failed += 1
        return ProduceSignalsOnceResult(len(pending), completed, failed)

    @staticmethod
    def _digest(*parts: str) -> str:
        return hashlib.sha256("\0".join(parts).encode()).hexdigest()
