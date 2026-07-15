from collections.abc import Sequence
from typing import Protocol

from .adoption import AuthorizedSignal
from .models import (
    ApprovedExecutionIntent,
    OrderResult,
    TradeCandidate,
)


class Strategy(Protocol):
    def evaluate(self, signals: Sequence[AuthorizedSignal]) -> TradeCandidate | None: ...


class BrokerGateway(Protocol):
    def submit(self, intent: ApprovedExecutionIntent) -> OrderResult: ...


class IdempotencyStore(Protocol):
    def claim(self, key: str) -> bool: ...
