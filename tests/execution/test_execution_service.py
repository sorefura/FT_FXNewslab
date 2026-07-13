from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest
from fx_core import CurrencyPair
from swap_bot.execution import ExecutionService
from swap_bot.idempotency import SQLiteIdempotencyStore
from swap_bot.models import (
    ApprovedExecutionIntent,
    CandidateId,
    ExecutionIntentId,
    OrderResult,
    OrderStatus,
    RiskDecisionId,
    Side,
)

NOW = datetime(2026, 7, 13, tzinfo=UTC)


class RejectingBrokerGateway:
    def submit(self, intent: ApprovedExecutionIntent) -> OrderResult:
        raise AssertionError("dry-run execution reached BrokerGateway.submit")


def _intent(key: str = "key-1") -> ApprovedExecutionIntent:
    return ApprovedExecutionIntent(
        intent_id=ExecutionIntentId(f"intent:{key}"),
        candidate_id=CandidateId("candidate-1"),
        risk_decision_id=RiskDecisionId("risk-1"),
        pair=CurrencyPair.parse("USD_JPY"),
        side=Side.BUY,
        quantity=Decimal("1000"),
        idempotency_key=key,
        created_at=NOW,
    )


def test_execution_accepts_approved_intent_and_never_submits_in_execplan_0001(
    tmp_path: Path,
) -> None:
    service = ExecutionService(
        SQLiteIdempotencyStore(tmp_path / "execution.sqlite3"), RejectingBrokerGateway()
    )
    result = service.submit(_intent())
    assert result.status is OrderStatus.NOT_SUBMITTED


def test_execution_rejects_non_approved_input(tmp_path: Path) -> None:
    service = ExecutionService(
        SQLiteIdempotencyStore(tmp_path / "execution.sqlite3"), RejectingBrokerGateway()
    )
    with pytest.raises(TypeError, match="ApprovedExecutionIntent"):
        service.submit(cast(ApprovedExecutionIntent, object()))


def test_execution_persistently_rejects_duplicate_idempotency_key(tmp_path: Path) -> None:
    database = tmp_path / "execution.sqlite3"
    first_service = ExecutionService(SQLiteIdempotencyStore(database), RejectingBrokerGateway())
    second_service = ExecutionService(SQLiteIdempotencyStore(database), RejectingBrokerGateway())
    assert first_service.submit(_intent()).status is OrderStatus.NOT_SUBMITTED
    assert second_service.submit(_intent()).status is OrderStatus.DUPLICATE
