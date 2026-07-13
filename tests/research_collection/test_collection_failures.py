from datetime import UTC, datetime
from pathlib import Path

import pytest
from fx_core import Currency, NewsObservation
from fx_research.application import CollectOnceService
from fx_research.collection import CollectedNewsItem
from fx_research.errors import SourceRetrievalError
from fx_research.normalization import NewsNormalizer
from fx_research.persistence import SQLiteIngestionStateStore
from fx_signal_store import SQLiteSignalStore

NOW = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)


class FailingNewsSource:
    source_id = "fed.press_monetary.rss"

    def fetch(self) -> tuple[CollectedNewsItem, ...]:
        raise SourceRetrievalError("recorded source outage api_key=recorded-secret")


class RecordedNewsSource:
    source_id = "fed.press_monetary.rss"

    def fetch(self) -> tuple[CollectedNewsItem, ...]:
        return tuple(
            CollectedNewsItem(
                source_id=self.source_id,
                candidate_currency=Currency("USD"),
                canonical_url=f"https://www.federalreserve.gov/item-{number}.htm",
                title=f"FOMC item {number}",
                body=f"Recorded policy body {number}",
                published_at=NOW,
                source_date_text="Mon, 13 Jul 2026 12:00:00 GMT",
                normalizer_version="fed-rss-v1",
            )
            for number in (1, 2)
        )


class FailingNormalizer(NewsNormalizer):
    def normalize(
        self, item: CollectedNewsItem, *, first_seen_at: datetime
    ) -> NewsObservation:
        raise ValueError("recorded normalization failure")


class FailSecondObservationStore(SQLiteSignalStore):
    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self._calls = 0

    def append_observation_if_absent(self, observation: NewsObservation) -> bool:
        self._calls += 1
        if self._calls == 2:
            raise RuntimeError("recorded persistence failure")
        return super().append_observation_if_absent(observation)


def test_collector_failure_creates_no_neutral_signal_or_live_side_effect(
    tmp_path: Path,
) -> None:
    database = tmp_path / "failed-fetch.sqlite3"
    signal_store = SQLiteSignalStore(database)
    state_store = SQLiteIngestionStateStore(database)

    with pytest.raises(SourceRetrievalError, match="recorded source outage"):
        CollectOnceService(signal_store, state_store).run(
            FailingNewsSource(), fetched_at=NOW
        )

    run = state_store.list_fetch_runs()[0]
    assert run.outcome == "RETRIEVAL_FAILED"
    assert run.processed_item_count == 0
    assert run.error_code == "SourceRetrievalError"
    assert run.error_message == "recorded source outage [REDACTED]"
    assert signal_store.list_signals() == ()
    assert state_store.pending_items(
        producer_version="producer-v1",
        model_version="model-v1",
        prompt_version="prompt-v1",
    ) == ()


def test_normalization_failure_is_recorded_without_success(tmp_path: Path) -> None:
    database = tmp_path / "normalization-failure.sqlite3"
    state_store = SQLiteIngestionStateStore(database)

    with pytest.raises(ValueError, match="recorded normalization failure"):
        CollectOnceService(
            SQLiteSignalStore(database), state_store, normalizer=FailingNormalizer()
        ).run(RecordedNewsSource(), fetched_at=NOW)

    runs = state_store.list_fetch_runs()
    assert [run.outcome for run in runs] == ["NORMALIZATION_FAILED"]
    assert runs[0].processed_item_count == 0


def test_persistence_failure_is_fail_fast_and_retry_remains_idempotent(
    tmp_path: Path,
) -> None:
    database = tmp_path / "persistence-failure.sqlite3"
    state_store = SQLiteIngestionStateStore(database)
    source = RecordedNewsSource()
    failing_store = FailSecondObservationStore(database)

    with pytest.raises(RuntimeError, match="recorded persistence failure"):
        CollectOnceService(failing_store, state_store).run(source, fetched_at=NOW)

    failure = state_store.list_fetch_runs()[0]
    first_item, second_item = source.fetch()
    first_id = NewsNormalizer().observation_id(first_item)
    second_id = NewsNormalizer().observation_id(second_item)
    assert failure.outcome == "PERSISTENCE_FAILED"
    assert failure.item_count == 2
    assert failure.processed_item_count == 1
    assert SQLiteSignalStore(database).get_observation(first_id).observation_id == first_id
    with pytest.raises(KeyError):
        SQLiteSignalStore(database).get_observation(second_id)
    pending_after_failure = state_store.pending_items(
        producer_version="producer-v1",
        model_version="model-v1",
        prompt_version="prompt-v1",
    )
    assert [item.observation_id for item in pending_after_failure] == [first_id]

    recovered = CollectOnceService(
        SQLiteSignalStore(database), state_store
    ).run(source, fetched_at=NOW)

    assert recovered.inserted == 1
    assert recovered.duplicates == 1
    assert SQLiteSignalStore(database).get_observation(second_id).observation_id == second_id
    assert [run.outcome for run in state_store.list_fetch_runs()] == [
        "PERSISTENCE_FAILED",
        "SUCCESS",
    ]
