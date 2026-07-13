import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from fx_core import Currency
from fx_research import __main__ as research_cli
from fx_research.collection import CollectedNewsItem
from fx_research.persistence import SQLiteIngestionStateStore

NOW = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)


class RecordedNewsSource:
    source_id = "fed.press_monetary.rss"

    def fetch(self) -> tuple[CollectedNewsItem, ...]:
        return (
            CollectedNewsItem(
                source_id=self.source_id,
                candidate_currency=Currency("USD"),
                canonical_url="https://www.federalreserve.gov/cli-example.htm",
                title="Recorded FOMC statement",
                body="Recorded monetary policy content.",
                published_at=NOW,
                source_date_text="Mon, 13 Jul 2026 12:00:00 GMT",
                normalizer_version="fed-rss-v1",
            ),
        )


def test_collect_and_produce_one_shot_commands_share_versioned_lineage(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    database = tmp_path / "one-shot.sqlite3"
    monkeypatch.setattr(research_cli, "build_source", lambda *args, **kwargs: RecordedNewsSource())
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "fx_research",
            "collect-once",
            "--source",
            "fed.press_monetary.rss",
            "--database",
            str(database),
        ],
    )
    research_cli.main()
    collection = json.loads(capsys.readouterr().out)

    pending = SQLiteIngestionStateStore(database).pending_items(
        producer_version="recorded-feature-provider-v1",
        model_version="recorded-model-v1",
        prompt_version="currency-fundamental-prompt-v1",
    )
    provider_fixture = tmp_path / "provider.json"
    provider_fixture.write_text(
        json.dumps(
            {
                pending[0].observation_id.value: {
                    "event_type": "monetary_policy",
                    "factor_scores": {"monetary_policy": 0.4},
                    "impact_strength": 0.6,
                    "confidence": 0.7,
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "fx_research",
            "produce-signals-once",
            "--database",
            str(database),
            "--provider-fixture",
            str(provider_fixture),
        ],
    )
    research_cli.main()
    production = json.loads(capsys.readouterr().out)

    assert collection["inserted"] == 1
    assert production == {"attempted": 1, "completed": 1, "failed": 0}
