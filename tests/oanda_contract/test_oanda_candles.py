import json
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from fx_core import CurrencyPair
from fx_research.errors import MarketDataError
from fx_research.infrastructure.oanda import OandaV20CandleSource

FIXTURE = Path(__file__).parents[1] / "fixtures" / "oanda_usdjpy_m1.json"


class RecordedTransport:
    def __init__(self, payload: Mapping[str, Any]) -> None:
        self.payload = payload
        self.calls: list[tuple[str, Mapping[str, str], float]] = []

    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> Mapping[str, Any]:
        self.calls.append((url, headers, timeout_seconds))
        return self.payload


def _source(transport: RecordedTransport) -> OandaV20CandleSource:
    return OandaV20CandleSource(
        transport,
        api_token="synthetic-oanda-token-for-tests-only",
        base_url="https://recorded-oanda.invalid",
        timeout_seconds=7.5,
    )


def test_oanda_request_uses_m1_midpoint_unsmoothed_rfc3339_contract() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    transport = RecordedTransport(payload)
    source = _source(transport)
    start = datetime(2026, 7, 14, 0, 0, tzinfo=UTC)
    end = datetime(2026, 7, 14, 0, 2, tzinfo=UTC)

    candles = source.fetch_candles(
        instrument=CurrencyPair.parse("USD_JPY"),
        granularity="M1",
        price_basis="midpoint",
        start_at=start,
        end_at=end,
    )

    url, headers, timeout = transport.calls[0]
    parsed = urlparse(url)
    assert parsed.path == "/v3/instruments/USD_JPY/candles"
    assert parse_qs(parsed.query) == {
        "price": ["M"],
        "granularity": ["M1"],
        "from": ["2026-07-14T00:00:00Z"],
        "to": ["2026-07-14T00:02:00Z"],
        "smooth": ["false"],
        "includeFirst": ["true"],
    }
    assert headers["Authorization"] == "Bearer synthetic-oanda-token-for-tests-only"
    assert headers["Accept-Datetime-Format"] == "RFC3339"
    assert timeout == 7.5
    assert len(candles) == 1
    assert candles[0].complete
    assert candles[0].open == Decimal("150.100")
    assert candles[0].open_time == start


def test_oanda_incomplete_candles_are_never_returned() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    transport = RecordedTransport(payload)

    candles = _source(transport).fetch_candles(
        instrument=CurrencyPair.parse("USD_JPY"),
        granularity="M1",
        price_basis="midpoint",
        start_at=datetime(2026, 7, 14, 0, 0, tzinfo=UTC),
        end_at=datetime(2026, 7, 14, 0, 2, tzinfo=UTC),
    )

    assert tuple(item.open_time.minute for item in candles) == (0,)


@pytest.mark.parametrize("response_instrument", ["USD_JPY", "USD/JPY"])
def test_oanda_accepts_equivalent_currency_pair_representations(
    response_instrument: str,
) -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["instrument"] = response_instrument

    candles = _source(RecordedTransport(payload)).fetch_candles(
        instrument=CurrencyPair.parse("USD_JPY"),
        granularity="M1",
        price_basis="midpoint",
        start_at=datetime(2026, 7, 14, 0, 0, tzinfo=UTC),
        end_at=datetime(2026, 7, 14, 0, 2, tzinfo=UTC),
    )

    assert candles[0].instrument == CurrencyPair.parse("USD_JPY")


@pytest.mark.parametrize(
    "payload",
    [
        {"instrument": "EUR_USD", "granularity": "M1", "candles": []},
        {
            "instrument": "USD_JPY",
            "granularity": "M1",
            "candles": [
                {"complete": True, "time": "2026-07-14T00:00:00Z"}
            ],
        },
    ],
)
def test_oanda_contract_mismatch_is_provider_failure_not_empty_market(
    payload: Mapping[str, Any],
) -> None:
    with pytest.raises(MarketDataError):
        _source(RecordedTransport(payload)).fetch_candles(
            instrument=CurrencyPair.parse("USD_JPY"),
            granularity="M1",
            price_basis="midpoint",
            start_at=datetime(2026, 7, 14, 0, 0, tzinfo=UTC),
            end_at=datetime(2026, 7, 14, 0, 2, tzinfo=UTC),
        )


@pytest.mark.parametrize(
    "response_instrument",
    [None, 42, "USDJPY", "USD__JPY", ""],
)
def test_oanda_rejects_missing_non_string_and_malformed_instrument(
    response_instrument: object,
) -> None:
    payload: dict[str, Any] = {
        "granularity": "M1",
        "candles": [],
    }
    if response_instrument is not None:
        payload["instrument"] = response_instrument

    with pytest.raises(MarketDataError):
        _source(RecordedTransport(payload)).fetch_candles(
            instrument=CurrencyPair.parse("USD_JPY"),
            granularity="M1",
            price_basis="midpoint",
            start_at=datetime(2026, 7, 14, 0, 0, tzinfo=UTC),
            end_at=datetime(2026, 7, 14, 0, 2, tzinfo=UTC),
        )
