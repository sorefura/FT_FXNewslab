from collections.abc import Sequence
from typing import Protocol

from fx_core import Signal

from .models import (
    ApprovedExecutionIntent,
    OrderResult,
    TradeCandidate,
)


class Strategy(Protocol):
    def evaluate(self, signals: Sequence[Signal]) -> TradeCandidate | None: ...


class BrokerGateway(Protocol):
    def submit(self, intent: ApprovedExecutionIntent) -> OrderResult: ...


class IdempotencyStore(Protocol):
    def claim(self, key: str) -> bool: ...

