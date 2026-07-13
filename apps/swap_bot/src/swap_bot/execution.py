import os
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol

from .models import (
    ApprovedExecutionIntent,
    OrderResult,
    OrderResultId,
    OrderStatus,
)
from .ports import IdempotencyStore


class ExecutionService:
    def __init__(self, idempotency_store: IdempotencyStore) -> None:
        self._idempotency_store = idempotency_store

    def submit(self, intent: ApprovedExecutionIntent) -> OrderResult:
        if not isinstance(intent, ApprovedExecutionIntent):
            raise TypeError("Execution accepts ApprovedExecutionIntent only")
        if not self._idempotency_store.claim(intent.idempotency_key):
            return OrderResult(
                result_id=OrderResultId(f"duplicate:{intent.intent_id.value}"),
                execution_intent_id=intent.intent_id,
                status=OrderStatus.DUPLICATE,
                filled_quantity=Decimal(0),
                broker_order_id=None,
                error_code="duplicate_idempotency_key",
                completed_at=datetime.now(UTC),
            )
        return OrderResult(
            result_id=OrderResultId(f"dry-run:{intent.intent_id.value}"),
            execution_intent_id=intent.intent_id,
            status=OrderStatus.NOT_SUBMITTED,
            filled_quantity=Decimal(0),
            broker_order_id=None,
            error_code=None,
            completed_at=datetime.now(UTC),
        )


@dataclass(frozen=True, slots=True)
class LiveArmPolicy:
    config_enabled: bool
    environment_variable: str = "LIVE_TRADING_ARMED"

    def is_armed(self) -> bool:
        return self.config_enabled and os.getenv(self.environment_variable) == "YES"


class HttpClient(Protocol):
    def post(self, url: str, *, data: str, headers: dict[str, str], timeout: float) -> Any: ...


class GmoPrivatePostTransport:
    def __init__(
        self, client: HttpClient, arm_policy: LiveArmPolicy, timeout: float = 10.0
    ) -> None:
        self._client = client
        self._arm_policy = arm_policy
        self._timeout = timeout

    def post_once(self, url: str, *, data: str, headers: dict[str, str]) -> Any:
        if not self._arm_policy.is_armed():
            raise PermissionError("Live trading requires both configuration and environment arming")
        return self._client.post(url, data=data, headers=headers, timeout=self._timeout)
