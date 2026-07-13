from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fx_core import Currency, Signal
from fx_research.application import CollectOnceService, ProduceSignalsOnceService
from fx_research.collection import CollectedNewsItem
from fx_research.feature_production import ProviderLlmFeatureExtractor, RecordedFeatureProvider
from fx_research.normalization import NewsNormalizer
from fx_research.persistence import SQLiteIngestionStateStore
from fx_signal_store import SQLiteSignalStore

NOW = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)


class AdvancingClock:
    def __init__(self, *values: datetime) -> None:
        self._values = iter(values)

    def __call__(self) -> datetime:
        return next(self._values)


class FailFirstSignalStore(SQLiteSignalStore):
    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self._should_fail = True

    def append_signal_if_absent(self, signal: Signal) -> bool:
        if self._should_fail:
            self._should_fail = False
            raise RuntimeError("recorded signal persistence interruption")
        return super().append_signal_if_absent(signal)


class RecordedNewsSource:
    source_id = "fed.press_monetary.rss"

    def fetch(self) -> tuple[CollectedNewsItem, ...]:
        return (
            CollectedNewsItem(
                source_id=self.source_id,
                candidate_currency=Currency("USD"),
                canonical_url="https://www.federalreserve.gov/example.htm",
                title="Federal Reserve issues FOMC statement",
                body="The Committee decided to maintain the target range.",
                published_at=NOW,
                source_date_text="Mon, 13 Jul 2026 12:00:00 GMT",
                normalizer_version="fed-rss-v1",
            ),
        )


def _stores(database: Path) -> tuple[SQLiteSignalStore, SQLiteIngestionStateStore]:
    return SQLiteSignalStore(database), SQLiteIngestionStateStore(database)


def _extractor(observation_id: str, response: dict[str, object]) -> ProviderLlmFeatureExtractor:
    return ProviderLlmFeatureExtractor(
        RecordedFeatureProvider({observation_id: response}),
        producer_version="llm-feature-v1",
        model_version="recorded-model-v1",
        prompt_version="currency-feature-prompt-v1",
        clock=lambda: NOW,
    )


def test_operational_pipeline_persists_versioned_observation_feature_signal_lineage(
    tmp_path: Path,
) -> None:
    signal_store, state_store = _stores(tmp_path / "pipeline.sqlite3")
    source = RecordedNewsSource()
    CollectOnceService(signal_store, state_store).run(source, fetched_at=NOW)
    observation_id = NewsNormalizer().observation_id(source.fetch()[0])
    extractor = _extractor(
        observation_id.value,
        {
            "event_type": "monetary_policy",
            "factor_scores": {"monetary_policy": 0.5, "inflation": 0.2},
            "impact_strength": 0.7,
            "confidence": 0.8,
        },
    )
    service = ProduceSignalsOnceService(signal_store, state_store, clock=lambda: NOW)

    first = service.run(extractor)
    second = service.run(extractor)
    signal = signal_store.list_signals(target="USD", scorer_version="fundamental-scorer-v1")[0]
    lineage = signal_store.get_lineage(signal.signal_id)

    assert first.completed == 1
    assert second.attempted == 0
    assert signal.versions.producer_version == "llm-feature-v1"
    assert signal.versions.model_version == "recorded-model-v1"
    assert signal.versions.prompt_version == "currency-feature-prompt-v1"
    assert signal.versions.scorer_version == "fundamental-scorer-v1"
    assert lineage.observation_ids == (observation_id,)


def test_production_timestamps_follow_observation_feature_signal_record_order(
    tmp_path: Path,
) -> None:
    signal_store, state_store = _stores(tmp_path / "chronology.sqlite3")
    source = RecordedNewsSource()
    CollectOnceService(signal_store, state_store).run(source, fetched_at=NOW)
    observation_id = NewsNormalizer().observation_id(source.fetch()[0])
    clock = AdvancingClock(
        NOW + timedelta(seconds=1),
        NOW + timedelta(seconds=2),
        NOW + timedelta(seconds=3),
    )
    extractor = ProviderLlmFeatureExtractor(
        RecordedFeatureProvider(
            {
                observation_id.value: {
                    "event_type": "monetary_policy",
                    "factor_scores": {"monetary_policy": 0.5},
                    "impact_strength": 0.7,
                    "confidence": 0.8,
                }
            }
        ),
        producer_version="llm-feature-v1",
        model_version="recorded-model-v1",
        prompt_version="currency-feature-prompt-v1",
        clock=clock,
    )

    result = ProduceSignalsOnceService(signal_store, state_store, clock=clock).run(
        extractor
    )

    signal = signal_store.list_signals()[0]
    feature = signal_store.get_feature(signal.source_feature_ids[0])
    record = state_store.get_production_record(
        observation_id=observation_id,
        producer_version="llm-feature-v1",
        model_version="recorded-model-v1",
        prompt_version="currency-feature-prompt-v1",
    )
    assert result.completed == 1
    assert NOW <= feature.created_at <= signal.created_at <= record.updated_at


def test_reused_feature_cannot_make_signal_or_record_predate_it(tmp_path: Path) -> None:
    database = tmp_path / "reused-feature.sqlite3"
    signal_store = FailFirstSignalStore(database)
    state_store = SQLiteIngestionStateStore(database)
    source = RecordedNewsSource()
    CollectOnceService(signal_store, state_store).run(source, fetched_at=NOW)
    observation_id = NewsNormalizer().observation_id(source.fetch()[0])
    feature_created_at = NOW + timedelta(seconds=5)
    extractor = ProviderLlmFeatureExtractor(
        RecordedFeatureProvider(
            {
                observation_id.value: {
                    "event_type": "monetary_policy",
                    "factor_scores": {"monetary_policy": 0.5},
                    "impact_strength": 0.7,
                    "confidence": 0.8,
                }
            }
        ),
        producer_version="llm-feature-v1",
        model_version="recorded-model-v1",
        prompt_version="currency-feature-prompt-v1",
        clock=lambda: feature_created_at,
    )
    interrupted = ProduceSignalsOnceService(
        signal_store,
        state_store,
        clock=AdvancingClock(
            NOW + timedelta(seconds=6),
            NOW + timedelta(seconds=7),
        ),
    ).run(extractor)
    signal_store = SQLiteSignalStore(database)
    recovered = ProduceSignalsOnceService(
        signal_store,
        state_store,
        clock=AdvancingClock(
            NOW + timedelta(seconds=2),
            NOW + timedelta(seconds=8),
        ),
    ).run(extractor)

    signal = signal_store.list_signals()[0]
    feature = signal_store.get_feature(signal.source_feature_ids[0])
    record = state_store.get_production_record(
        observation_id=observation_id,
        producer_version="llm-feature-v1",
        model_version="recorded-model-v1",
        prompt_version="currency-feature-prompt-v1",
    )
    assert interrupted.failed == 1
    assert recovered.completed == 1
    assert NOW <= feature.created_at <= signal.created_at <= record.updated_at


@pytest.mark.parametrize(
    "field", ["action", "side", "quantity", "leverage", "order", "order_action"]
)
def test_forbidden_llm_action_field_creates_no_signal(tmp_path: Path, field: str) -> None:
    signal_store, state_store = _stores(tmp_path / "pipeline.sqlite3")
    source = RecordedNewsSource()
    CollectOnceService(signal_store, state_store).run(source, fetched_at=NOW)
    observation_id = NewsNormalizer().observation_id(source.fetch()[0])
    extractor = _extractor(
        observation_id.value,
        {
            "event_type": "monetary_policy",
            "factor_scores": {"monetary_policy": 0.5},
            "impact_strength": 0.7,
            "confidence": 0.8,
            field: "BUY",
        },
    )

    result = ProduceSignalsOnceService(
        signal_store, state_store, clock=lambda: NOW
    ).run(extractor)

    assert result.failed == 1
    assert signal_store.list_signals() == ()
