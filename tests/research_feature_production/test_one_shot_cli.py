import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fx_core import Currency
from fx_research import __main__ as research_cli
from fx_research.application import ProduceSignalsOnceResult
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
    assert research_cli.main() == 0
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
            "--provider",
            "recorded",
            "--provider-fixture",
            str(provider_fixture),
        ],
    )
    assert research_cli.main() == 0
    production = json.loads(capsys.readouterr().out)

    assert collection["inserted"] == 1
    assert production == {"attempted": 1, "completed": 1, "failed": 0}


@pytest.mark.parametrize(
    ("result", "expected_exit_code"),
    [
        (ProduceSignalsOnceResult(attempted=2, completed=2, failed=0), 0),
        (ProduceSignalsOnceResult(attempted=2, completed=1, failed=1), 1),
        (ProduceSignalsOnceResult(attempted=2, completed=0, failed=2), 1),
    ],
)
def test_production_exit_code_reports_item_failures(
    tmp_path: Path,
    monkeypatch,
    capsys,
    result: ProduceSignalsOnceResult,
    expected_exit_code: int,
) -> None:
    fixture = tmp_path / "provider.json"
    fixture.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        research_cli.ProduceSignalsOnceService,
        "run",
        lambda self, extractor: result,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "fx_research",
            "produce-signals-once",
            "--database",
            str(tmp_path / "exit-code.sqlite3"),
            "--provider",
            "recorded",
            "--provider-fixture",
            str(fixture),
        ],
    )

    exit_code = research_cli.main()

    assert exit_code == expected_exit_code
    assert json.loads(capsys.readouterr().out) == {
        "attempted": result.attempted,
        "completed": result.completed,
        "failed": result.failed,
    }


def test_partial_success_requires_explicit_cli_opt_in(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    fixture = tmp_path / "provider.json"
    fixture.write_text("{}", encoding="utf-8")
    result = ProduceSignalsOnceResult(attempted=2, completed=1, failed=1)
    monkeypatch.setattr(
        research_cli.ProduceSignalsOnceService,
        "run",
        lambda self, extractor: result,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "fx_research",
            "produce-signals-once",
            "--database",
            str(tmp_path / "partial.sqlite3"),
            "--provider",
            "recorded",
            "--provider-fixture",
            str(fixture),
            "--allow-partial-success",
        ],
    )

    assert research_cli.main() == 0
    assert json.loads(capsys.readouterr().out)["failed"] == 1


@pytest.mark.parametrize(
    "arguments",
    [
        ["--provider", "unknown"],
        ["--provider", "recorded"],
    ],
)
def test_invalid_provider_configuration_exits_nonzero(
    tmp_path: Path, monkeypatch, arguments: list[str]
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "fx_research",
            "produce-signals-once",
            "--database",
            str(tmp_path / "invalid.sqlite3"),
            *arguments,
        ],
    )

    with pytest.raises(SystemExit) as exit_info:
        research_cli.main()

    assert exit_info.value.code != 0


def test_openai_provider_requires_environment_credential(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "fx_research",
            "produce-signals-once",
            "--database",
            str(tmp_path / "openai.sqlite3"),
            "--provider",
            "openai",
            "--model",
            "recorded-openai-model",
        ],
    )

    with pytest.raises(SystemExit) as exit_info:
        research_cli.main()

    assert exit_info.value.code != 0


def test_openai_provider_does_not_require_fixture(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-recorded-not-sent")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "fx_research",
            "produce-signals-once",
            "--database",
            str(tmp_path / "openai-empty.sqlite3"),
            "--provider",
            "openai",
            "--model",
            "recorded-openai-model",
        ],
    )

    assert research_cli.main() == 0
    assert json.loads(capsys.readouterr().out) == {
        "attempted": 0,
        "completed": 0,
        "failed": 0,
    }
