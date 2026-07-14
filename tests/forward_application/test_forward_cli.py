import json
import sys
from pathlib import Path

from fx_research import __main__ as research_cli
from fx_research.forward_application import ObserveForwardOnceResult


def test_observe_forward_once_cli_defaults_to_gmo_fx_primary_provider(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    expected = ObserveForwardOnceResult(
        signals_scanned=1,
        unsupported_signals=0,
        jobs_scheduled=5,
        due_jobs=5,
        pending_jobs=0,
        completed=5,
        failed=0,
        unavailable=0,
    )
    selected: list[str] = []
    monkeypatch.setattr(
        research_cli,
        "_build_market_source",
        lambda args, parser: selected.append(args.provider) or object(),
    )
    monkeypatch.setattr(
        research_cli.ObserveForwardOnceService,
        "run",
        lambda self, source, *, instrument: expected,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "fx_research",
            "observe-forward-once",
            "--database",
            str(tmp_path / "forward.sqlite3"),
            "--pair",
            "USD_JPY",
        ],
    )

    assert research_cli.main() == 0
    assert selected == ["gmo-fx"]
    assert json.loads(capsys.readouterr().out) == {
        "completed": 5,
        "due_jobs": 5,
        "failed": 0,
        "jobs_scheduled": 5,
        "pending_jobs": 0,
        "signals_scanned": 1,
        "unavailable": 0,
        "unsupported_signals": 0,
    }


def test_observe_forward_once_cli_requires_oanda_token(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("OANDA_API_TOKEN", raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "fx_research",
            "observe-forward-once",
            "--database",
            str(tmp_path / "forward.sqlite3"),
            "--provider",
            "oanda",
            "--pair",
            "USD_JPY",
        ],
    )

    try:
        research_cli.main()
    except SystemExit as error:
        assert error.code != 0
    else:
        raise AssertionError("missing OANDA credential must stop the command")
