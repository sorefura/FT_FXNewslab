import json
from collections.abc import Mapping
from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from fx_core import CurrencyPair
from fx_research.errors import MarketDataError
from fx_research.infrastructure.gmo_fx import GmoFxMarketDataSource

FIXTURE = Path(__file__).parents[1] / "fixtures" / "gmo_fx_usdjpy_bid_m1.json"


class RecordedTransport:
    def __init__(self, responses: list[Mapping[str, Any]]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, float]] = []

    def get(self, url: str, *, timeout_seconds: float) -> Mapping[str, Any]:
        self.calls.append((url, timeout_seconds))
        index = min(len(self.calls) - 1, len(self.responses) - 1)
        return self.responses[index]


def _payload() -> dict[str, Any]:
    value = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _source(transport: RecordedTransport) -> GmoFxMarketDataSource:
    return GmoFxMarketDataSource(
        transport,
        base_url="https://recorded-gmo-fx.invalid/public",
        timeout_seconds=7.5,
    )


def test_gmo_fx_public_request_normalizes_m1_bid_ohlc_and_utc_timestamp() -> None:
    transport = RecordedTransport([_payload()])
    source = _source(transport)
    start = datetime(2026, 7, 14, 0, 0, tzinfo=UTC)

    candles = source.fetch_candles(
        instrument=CurrencyPair.parse("USD_JPY"),
        granularity="M1",
        price_basis="bid",
        start_at=start,
        end_at=datetime(2026, 7, 14, 0, 3, tzinfo=UTC),
    )

    assert len(transport.calls) == 2
    first_url, timeout = transport.calls[0]
    parsed = urlparse(first_url)
    assert parsed.path == "/public/v1/klines"
    assert parse_qs(parsed.query) == {
        "symbol": ["USD_JPY"],
        "priceType": ["BID"],
        "interval": ["1min"],
        "date": ["20260713"],
    }
    assert "API-KEY" not in first_url
    assert timeout == 7.5
    assert tuple(item.open_time for item in candles) == (
        start,
        datetime(2026, 7, 14, 0, 1, tzinfo=UTC),
    )
    assert candles[0].instrument == CurrencyPair.parse("USD_JPY")
    assert candles[0].open == Decimal("161.800")
    assert candles[0].high == Decimal("161.820")
    assert candles[0].low == Decimal("161.790")
    assert candles[0].close == Decimal("161.810")
    assert candles[0].price_basis == "bid"
    assert candles[0].market_data_version == "gmo-fx-kline-bid-v1"


def test_gmo_fx_excludes_candle_until_response_time_reaches_m1_close() -> None:
    source = _source(RecordedTransport([_payload()]))

    candles = source.fetch_candles(
        instrument=CurrencyPair.parse("USD_JPY"),
        granularity="M1",
        price_basis="bid",
        start_at=datetime(2026, 7, 14, 0, 0, tzinfo=UTC),
        end_at=datetime(2026, 7, 14, 0, 3, tzinfo=UTC),
    )

    assert all(item.complete for item in candles)
    assert datetime(2026, 7, 14, 0, 2, tzinfo=UTC) not in {
        item.open_time for item in candles
    }


def test_gmo_fx_three_day_range_splitting_is_bounded_and_deterministic() -> None:
    empty = {"status": 0, "data": [], "responsetime": "2026-07-14T00:00:00Z"}
    transport = RecordedTransport([empty])

    source = _source(transport)
    source.fetch_candles(
        instrument=CurrencyPair.parse("USD_JPY"),
        granularity="M1",
        price_basis="bid",
        start_at=datetime(2026, 7, 10, 20, 50, tzinfo=UTC),
        end_at=datetime(2026, 7, 13, 20, 50, tzinfo=UTC),
    )
    source.fetch_candles(
        instrument=CurrencyPair.parse("USD_JPY"),
        granularity="M1",
        price_basis="bid",
        start_at=datetime(2026, 7, 10, 20, 50, tzinfo=UTC),
        end_at=datetime(2026, 7, 13, 20, 50, tzinfo=UTC),
    )

    assert [parse_qs(urlparse(call[0]).query)["date"][0] for call in transport.calls] == [
        "20260710",
        "20260711",
        "20260712",
        "20260713",
        "20260714",
    ]


def test_gmo_fx_fetches_each_provider_date_once_for_repeated_signal_ranges() -> None:
    transport = RecordedTransport([_payload()])
    source = _source(transport)
    request = {
        "instrument": CurrencyPair.parse("USD_JPY"),
        "granularity": "M1",
        "price_basis": "bid",
        "start_at": datetime(2026, 7, 14, 0, 0, tzinfo=UTC),
        "end_at": datetime(2026, 7, 14, 0, 3, tzinfo=UTC),
    }

    first = source.fetch_candles(**request)
    second = source.fetch_candles(**request)

    assert first == second
    assert len(transport.calls) == 2
    assert [parse_qs(urlparse(call[0]).query)["date"][0] for call in transport.calls] == [
        "20260713",
        "20260714",
    ]


def test_gmo_fx_failed_provider_response_is_not_cached() -> None:
    failed = {
        "status": 1,
        "data": [],
        "responsetime": "2026-07-14T00:03:00Z",
    }
    transport = RecordedTransport([failed, _payload(), _payload()])
    source = _source(transport)
    request = {
        "instrument": CurrencyPair.parse("USD_JPY"),
        "granularity": "M1",
        "price_basis": "bid",
        "start_at": datetime(2026, 7, 14, 0, 0, tzinfo=UTC),
        "end_at": datetime(2026, 7, 14, 0, 3, tzinfo=UTC),
    }

    with pytest.raises(MarketDataError):
        source.fetch_candles(**request)
    source.fetch_candles(**request)

    provider_dates = [
        parse_qs(urlparse(call[0]).query)["date"][0] for call in transport.calls
    ]
    assert provider_dates == ["20260713", "20260713", "20260714"]


def test_gmo_fx_deduplicates_same_content_but_preserves_changed_revision() -> None:
    original = _payload()
    changed = deepcopy(original)
    changed["data"] = [deepcopy(original["data"][0])]
    changed["data"][0]["close"] = "161.811"
    changed["responsetime"] = "2026-07-14T00:03:00Z"
    transport = RecordedTransport([original, changed])

    candles = _source(transport).fetch_candles(
        instrument=CurrencyPair.parse("USD_JPY"),
        granularity="M1",
        price_basis="bid",
        start_at=datetime(2026, 7, 14, 0, 0, tzinfo=UTC),
        end_at=datetime(2026, 7, 14, 0, 3, tzinfo=UTC),
    )

    at_midnight = [
        item for item in candles if item.open_time == datetime(2026, 7, 14, tzinfo=UTC)
    ]
    assert len(at_midnight) == 2
    assert len({item.revision_id for item in at_midnight}) == 2


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload["data"][0].pop("openTime"),
        lambda payload: payload["data"][0].update(open="not-a-decimal"),
        lambda payload: payload.update(responsetime="not-a-time"),
    ],
)
def test_gmo_fx_rejects_missing_timestamp_and_malformed_market_data(
    mutation: Any,
) -> None:
    payload = _payload()
    mutation(payload)

    with pytest.raises(MarketDataError):
        _source(RecordedTransport([payload])).fetch_candles(
            instrument=CurrencyPair.parse("USD_JPY"),
            granularity="M1",
            price_basis="bid",
            start_at=datetime(2026, 7, 14, 0, 0, tzinfo=UTC),
            end_at=datetime(2026, 7, 14, 0, 3, tzinfo=UTC),
        )


def test_gmo_fx_does_not_synthesize_midpoint_extrema() -> None:
    with pytest.raises(MarketDataError, match="BID"):
        _source(RecordedTransport([_payload()])).fetch_candles(
            instrument=CurrencyPair.parse("USD_JPY"),
            granularity="M1",
            price_basis="midpoint",
            start_at=datetime(2026, 7, 14, 0, 0, tzinfo=UTC),
            end_at=datetime(2026, 7, 14, 0, 3, tzinfo=UTC),
        )
