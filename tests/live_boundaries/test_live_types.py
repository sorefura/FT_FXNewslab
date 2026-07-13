from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fx_core import CurrencyPair, Probability, SignalId
from swap_bot.models import (
    CandidateId,
    ExecutionIntentId,
    PortfolioDecisionId,
    RiskDecisionId,
    Side,
    TradeCandidate,
)

NOW = datetime(2026, 7, 13, tzinfo=UTC)


def candidate() -> TradeCandidate:
    return TradeCandidate(
        candidate_id=CandidateId("candidate-1"),
        strategy_id="fixture",
        strategy_version="fixture-v1",
        pair=CurrencyPair.parse("USD_JPY"),
        side=Side.BUY,
        score=Probability(0.8),
        signal_ids=(SignalId("signal-1"),),
        created_at=NOW,
    )


def test_trade_candidate_records_strategy_and_signal_lineage() -> None:
    created = candidate()
    assert created.strategy_version == "fixture-v1"
    assert created.signal_ids == (SignalId("signal-1"),)


@pytest.mark.parametrize(
    "identifier",
    [
        CandidateId("candidate"),
        PortfolioDecisionId("portfolio"),
        RiskDecisionId("risk"),
        ExecutionIntentId("intent"),
    ],
)
def test_live_identifiers_remain_distinct_types(identifier: object) -> None:
    assert type(identifier) is not str
    assert Decimal("1") > 0
