from datetime import UTC, datetime
from pathlib import Path

import pytest
from fx_research.application import CollectOnceService
from fx_research.errors import SourceRetrievalError
from fx_research.persistence import SQLiteIngestionStateStore
from fx_signal_store import SQLiteSignalStore

NOW = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)


class FailingNewsSource:
    source_id = "fed.press_monetary.rss"

    def fetch(self) -> tuple[object, ...]:
        raise SourceRetrievalError("recorded source outage")


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

    assert signal_store.list_signals() == ()
    assert state_store.pending_items(
        producer_version="producer-v1",
        model_version="model-v1",
        prompt_version="prompt-v1",
    ) == ()
