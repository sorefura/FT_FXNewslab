import json
from pathlib import Path

from swap_bot.models import ApprovedExecutionIntent, OrderResult
from swap_bot.shadow import run_fixture_file

ROOT = Path(__file__).parents[2]


class CountingBrokerGateway:
    def __init__(self) -> None:
        self.submit_calls = 0

    def submit(self, intent: ApprovedExecutionIntent) -> OrderResult:
        self.submit_calls += 1
        raise AssertionError("shadow cycle reached BrokerGateway.submit")


def test_shadow_cycle_persists_complete_chain_without_broker_submission(tmp_path: Path) -> None:
    gateway = CountingBrokerGateway()
    result = run_fixture_file(
        ROOT / "tests/fixtures/shadow_cycle.json",
        tmp_path / "shadow.sqlite3",
        gateway,
    )
    assert result["decision_chain_complete"] is True
    assert result["decision_chain_consistent"] is True
    assert result["order_status"] == "NOT_SUBMITTED"
    assert gateway.submit_calls == 0
    assert "broker_submit_calls" not in result
    assert result["feature_ids"] == ["feature-jpy-1", "feature-usd-1"]
    assert result["observation_ids"] == ["obs-shadow-1"]


def test_shadow_fixture_contains_no_live_enablement() -> None:
    fixture = json.loads((ROOT / "tests/fixtures/shadow_cycle.json").read_text(encoding="utf-8"))
    assert "enable_live_trading" not in fixture
    assert "broker" not in fixture
