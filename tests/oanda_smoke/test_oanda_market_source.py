import os
from datetime import UTC, datetime, timedelta

import pytest
from fx_core import CurrencyPair
from fx_research.infrastructure.oanda import OandaV20CandleSource, UrllibOandaTransport


@pytest.mark.oanda_smoke
def test_oanda_returns_complete_usdjpy_m1_midpoint_candles() -> None:
    if os.getenv("RUN_OANDA_SMOKE") != "1":
        pytest.skip("set RUN_OANDA_SMOKE=1 to enable OANDA smoke")
    token = os.getenv("OANDA_API_TOKEN")
    base_url = os.getenv("OANDA_API_BASE_URL")
    if not token or not base_url:
        pytest.skip("OANDA_API_TOKEN and OANDA_API_BASE_URL are required")
    source = OandaV20CandleSource(
        UrllibOandaTransport(),
        api_token=token,
        base_url=base_url,
        timeout_seconds=float(os.getenv("OANDA_API_TIMEOUT_SECONDS", "10")),
    )
    end = datetime.now(UTC) - timedelta(minutes=1)

    candles = source.fetch_candles(
        instrument=CurrencyPair.parse("USD_JPY"),
        granularity="M1",
        price_basis="midpoint",
        start_at=end - timedelta(minutes=10),
        end_at=end,
    )

    assert candles
    assert all(item.complete for item in candles)
