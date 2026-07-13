from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Protocol

from fx_core import CurrencyPair
from fx_core.time import require_utc


class SwapAvailability(Enum):
    AVAILABLE = "AVAILABLE"
    UNKNOWN = "UNKNOWN"
    UNAVAILABLE = "UNAVAILABLE"
    STALE = "STALE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass(frozen=True, slots=True)
class SwapQuote:
    pair: CurrencyPair
    availability: SwapAvailability
    long_per_day: Decimal | None
    short_per_day: Decimal | None
    source: str
    observed_at: datetime
    effective_from: datetime | None
    effective_until: datetime | None

    def __post_init__(self) -> None:
        if not self.source.strip():
            raise ValueError("Swap source is required")
        require_utc(self.observed_at, "swap.observed_at")
        if self.effective_from is not None:
            require_utc(self.effective_from, "swap.effective_from")
        if self.effective_until is not None:
            require_utc(self.effective_until, "swap.effective_until")
        if self.availability is SwapAvailability.AVAILABLE and (
            self.long_per_day is None or self.short_per_day is None
        ):
            raise ValueError("Available SwapQuote requires long and short values")
        if self.availability in {
            SwapAvailability.UNKNOWN,
            SwapAvailability.UNAVAILABLE,
            SwapAvailability.NOT_APPLICABLE,
        } and (self.long_per_day is not None or self.short_per_day is not None):
            raise ValueError("Unknown or unavailable swap cannot contain numeric zero/value")

    def is_usable(self, as_of: datetime, max_age: timedelta) -> bool:
        require_utc(as_of, "swap.as_of")
        if self.availability is not SwapAvailability.AVAILABLE:
            return False
        if as_of - self.observed_at > max_age:
            return False
        if self.effective_from is not None and as_of < self.effective_from:
            return False
        return self.effective_until is None or as_of <= self.effective_until


class SwapDataSource(Protocol):
    def get_quote(self, pair: CurrencyPair, as_of: datetime) -> SwapQuote: ...


class ManualOverrideSwapSource:
    def __init__(self, quotes: dict[CurrencyPair, SwapQuote]) -> None:
        self._quotes = dict(quotes)

    def get_quote(self, pair: CurrencyPair, as_of: datetime) -> SwapQuote:
        return self._quotes.get(
            pair,
            SwapQuote(
                pair=pair,
                availability=SwapAvailability.UNKNOWN,
                long_per_day=None,
                short_per_day=None,
                source="manual_override",
                observed_at=as_of,
                effective_from=None,
                effective_until=None,
            ),
        )


class SwapSourceSelector:
    def __init__(self, sources: Sequence[SwapDataSource], max_age: timedelta) -> None:
        if not sources or max_age <= timedelta(0):
            raise ValueError("Swap source priority and max_age are required")
        self._sources = tuple(sources)
        self._max_age = max_age

    def select(self, pair: CurrencyPair, as_of: datetime) -> SwapQuote:
        first_result: SwapQuote | None = None
        for source in self._sources:
            quote = source.get_quote(pair, as_of)
            first_result = first_result or quote
            if quote.is_usable(as_of, self._max_age):
                return quote
            if (
                quote.availability is SwapAvailability.AVAILABLE
                and as_of - quote.observed_at > self._max_age
            ):
                first_result = replace(quote, availability=SwapAvailability.STALE)
        if first_result is None:
            raise RuntimeError("Swap source priority unexpectedly empty")
        return first_result

