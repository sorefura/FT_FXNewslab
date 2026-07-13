import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fx_core import CurrencyPair, Probability, SignalId
from swap_bot.decision_store import SQLiteLiveDecisionStore
from swap_bot.models import (
    ApprovedExecutionIntent,
    CandidateId,
    ExecutionIntentId,
    RiskDecisionId,
    Side,
    TradeCandidate,
)
from swap_bot.shadow import run_fixture_file

ROOT = Path(__file__).parents[2]
NOW = datetime(2026, 7, 13, tzinfo=UTC)


def test_decision_store_rejects_intent_whose_risk_chain_belongs_to_another_candidate(
    tmp_path: Path,
) -> None:
    database = tmp_path / "decision-chain.sqlite3"
    run_fixture_file(ROOT / "tests/fixtures/shadow_cycle.json", database)
    store = SQLiteLiveDecisionStore(database)
    other_candidate = TradeCandidate(
        candidate_id=CandidateId("candidate-shadow-2"),
        strategy_id="shadow-fixture",
        strategy_version="shadow-fixture-v1",
        pair=CurrencyPair.parse("USD_JPY"),
        side=Side.BUY,
        score=Probability(0.8),
        signal_ids=(SignalId("signal-usdjpy-1"),),
        created_at=NOW,
    )
    store.append_candidate(other_candidate)
    forged_intent = ApprovedExecutionIntent(
        intent_id=ExecutionIntentId("intent-shadow-forged"),
        candidate_id=other_candidate.candidate_id,
        risk_decision_id=RiskDecisionId("risk-shadow-1"),
        pair=other_candidate.pair,
        side=other_candidate.side,
        quantity=Decimal("1000"),
        idempotency_key="shadow-cycle-forged",
        created_at=NOW,
    )

    with pytest.raises(sqlite3.IntegrityError, match="decision chain is inconsistent"):
        store.append_intent(forged_intent)
