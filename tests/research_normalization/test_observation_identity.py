from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fx_core import Currency
from fx_research.application import CollectOnceService
from fx_research.collection import CollectedNewsItem
from fx_research.normalization import NewsNormalizer
from fx_research.persistence import SQLiteIngestionStateStore
from fx_signal_store import SQLiteSignalStore

NOW = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)


class RecordedNewsSource:
    def __init__(self, item: CollectedNewsItem) -> None:
        self._item = item

    @property
    def source_id(self) -> str:
        return self._item.source_id

    def fetch(self) -> tuple[CollectedNewsItem, ...]:
        return (self._item,)


def _item() -> CollectedNewsItem:
    return CollectedNewsItem(
        source_id="boj.monetary_policy.html",
        candidate_currency=Currency("JPY"),
        canonical_url="https://www.boj.or.jp/example.htm#section",
        title="  Statement\u3000on Monetary Policy  ",
        body="Policy   text\nwith repeated whitespace.",
        published_at=None,
        source_date_text="July 13, 2026",
        normalizer_version="boj-html-v1",
    )


def _service(
    database: Path,
) -> tuple[CollectOnceService, SQLiteSignalStore, SQLiteIngestionStateStore]:
    signal_store = SQLiteSignalStore(database)
    state_store = SQLiteIngestionStateStore(database)
    return CollectOnceService(signal_store, state_store), signal_store, state_store


def test_repeated_poll_preserves_first_seen_at_and_creates_one_observation(
    tmp_path: Path,
) -> None:
    service, signal_store, _ = _service(tmp_path / "news.sqlite3")
    source = RecordedNewsSource(_item())

    first = service.run(source, fetched_at=NOW)
    second = service.run(source, fetched_at=NOW + timedelta(minutes=5))
    observation_id = NewsNormalizer().observation_id(_item())
    observation = signal_store.get_observation(observation_id)

    assert first.inserted == 1
    assert second.inserted == 0
    assert second.duplicates == 1
    assert observation.first_seen_at == NOW


def test_same_url_with_changed_normalized_content_creates_new_observation(
    tmp_path: Path,
) -> None:
    service, signal_store, _ = _service(tmp_path / "news.sqlite3")
    first_item = _item()
    changed_item = replace(first_item, body="Materially changed policy text.")

    service.run(RecordedNewsSource(first_item), fetched_at=NOW)
    service.run(RecordedNewsSource(changed_item), fetched_at=NOW + timedelta(minutes=5))

    first_id = NewsNormalizer().observation_id(first_item)
    changed_id = NewsNormalizer().observation_id(changed_item)
    assert first_id != changed_id
    first_body = signal_store.get_observation(first_id).body
    changed_body = signal_store.get_observation(changed_id).body
    assert first_body != changed_body


def test_date_only_source_metadata_is_evidence_not_publication_timestamp(
    tmp_path: Path,
) -> None:
    service, signal_store, state_store = _service(tmp_path / "news.sqlite3")
    item = _item()
    service.run(RecordedNewsSource(item), fetched_at=NOW)
    observation_id = NewsNormalizer().observation_id(item)

    observation = signal_store.get_observation(observation_id)
    evidence = state_store.get_ingestion_evidence(observation_id)

    assert observation.published_at is None
    assert evidence.source_date_text == "July 13, 2026"
    assert evidence.first_seen_at == NOW
