from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fx_core import CurrencyPair
from swap_bot.swap import (
    ManualOverrideSwapSource,
    SwapAvailability,
    SwapQuote,
    SwapSourceSelector,
)

NOW = datetime(2026, 7, 13, tzinfo=UTC)
PAIR = CurrencyPair.parse("USD_JPY")


def test_unknown_swap_cannot_be_represented_as_numeric_zero() -> None:
    with pytest.raises(ValueError):
        SwapQuote(
            pair=PAIR,
            availability=SwapAvailability.UNKNOWN,
            long_per_day=Decimal(0),
            short_per_day=Decimal(0),
            source="fixture",
            observed_at=NOW,
            effective_from=None,
            effective_until=None,
        )


def test_selector_marks_expired_value_stale_and_preserves_source_identity() -> None:
    quote = SwapQuote(
        pair=PAIR,
        availability=SwapAvailability.AVAILABLE,
        long_per_day=Decimal("10"),
        short_per_day=Decimal("-12"),
        source="manual_override",
        observed_at=NOW - timedelta(days=2),
        effective_from=NOW - timedelta(days=3),
        effective_until=None,
    )
    selected = SwapSourceSelector(
        (ManualOverrideSwapSource({PAIR: quote}),), max_age=timedelta(days=1)
    ).select(PAIR, NOW)
    assert selected.availability is SwapAvailability.STALE
    assert selected.source == "manual_override"
    assert not selected.is_usable(NOW, timedelta(days=1))

