import os
from datetime import UTC, datetime

import pytest
from fx_core import CurrencyPair
from fx_research.infrastructure.gmo_fx import (
    GMO_FX_PUBLIC_BASE_URL,
    GmoFxMarketDataSource,
    UrllibGmoFxTransport,
)


@pytest.mark.gmo_fx_smoke
def test_gmo_fx_public_historical_m1_bid_path() -> None:
    if os.getenv("RUN_GMO_FX_SMOKE") != "1":
        pytest.skip("set RUN_GMO_FX_SMOKE=1 to call GMO FX Public API")
    source = GmoFxMarketDataSource(
        UrllibGmoFxTransport(),
        base_url=os.getenv("GMO_FX_PUBLIC_API_BASE_URL", GMO_FX_PUBLIC_BASE_URL),
        timeout_seconds=float(os.getenv("GMO_FX_API_TIMEOUT_SECONDS", "10")),
    )

    candles = source.fetch_candles(
        instrument=CurrencyPair.parse("USD_JPY"),
        granularity="M1",
        price_basis="bid",
        start_at=datetime(2026, 7, 10, 20, 50, tzinfo=UTC),
        end_at=datetime(2026, 7, 13, 20, 50, tzinfo=UTC),
    )

    assert len(candles) == 1380
    assert candles[0].open_time == datetime(2026, 7, 10, 20, 50, tzinfo=UTC)
    assert candles[-1].open_time == datetime(2026, 7, 13, 20, 49, tzinfo=UTC)
    assert all(item.complete and item.price_basis == "bid" for item in candles)
