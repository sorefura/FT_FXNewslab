import json
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from fx_core import CurrencyPair

from ..errors import MarketDataError
from ..forward import MarketCandle

OANDA_SOURCE = "oanda-v20"
OANDA_MARKET_DATA_VERSION = "oanda-v20-candles-v1"


class OandaTransport(Protocol):
    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> Mapping[str, Any]: ...


class UrllibOandaTransport:
    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> Mapping[str, Any]:
        request = Request(url, headers=dict(headers), method="GET")
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            raise MarketDataError(f"OANDA API returned HTTP {error.code}") from error
        except (TimeoutError, URLError, OSError) as error:
            raise MarketDataError(
                f"OANDA API request failed: {type(error).__name__}"
            ) from error
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise MarketDataError("OANDA API returned malformed JSON") from error
        if not isinstance(payload, Mapping):
            raise MarketDataError("OANDA API response must be an object")
        return payload


class OandaV20CandleSource:
    source = OANDA_SOURCE
    market_data_version = OANDA_MARKET_DATA_VERSION

    def __init__(
        self,
        transport: OandaTransport,
        *,
        api_token: str,
        base_url: str,
        timeout_seconds: float,
    ) -> None:
        if not api_token.strip():
            raise ValueError("OANDA_API_TOKEN must not be blank")
        if not base_url.strip():
            raise ValueError("OANDA_API_BASE_URL must not be blank")
        if timeout_seconds <= 0:
            raise ValueError("OANDA_API_TIMEOUT_SECONDS must be positive")
        self._transport = transport
        self._api_token = api_token
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    def fetch_candles(
        self,
        *,
        instrument: CurrencyPair,
        granularity: str,
        price_basis: str,
        start_at: datetime,
        end_at: datetime,
    ) -> tuple[MarketCandle, ...]:
        if instrument != CurrencyPair.parse("USD_JPY"):
            raise MarketDataError(f"unsupported OANDA instrument: {instrument.symbol}")
        if granularity != "M1" or price_basis != "midpoint":
            raise MarketDataError("OANDA forward evaluation requires M1 midpoint candles")
        query = urlencode(
            {
                "price": "M",
                "granularity": granularity,
                "from": _rfc3339(start_at),
                "to": _rfc3339(end_at),
                "smooth": "false",
                "includeFirst": "true",
            }
        )
        url = f"{self._base_url}/v3/instruments/{instrument.symbol}/candles?{query}"
        payload = self._transport.get(
            url,
            headers={
                "Authorization": f"Bearer {self._api_token}",
                "Accept-Datetime-Format": "RFC3339",
                "User-Agent": "FT-FXNewslab/0.1 forward-evaluation",
            },
            timeout_seconds=self._timeout_seconds,
        )
        self._validate_response_semantics(payload, instrument, granularity)
        raw_candles = payload.get("candles")
        if not isinstance(raw_candles, list):
            raise MarketDataError("OANDA response has no candles array")
        candles = tuple(
            self._parse_candle(item, instrument, granularity, price_basis)
            for item in raw_candles
            if isinstance(item, Mapping) and item.get("complete") is True
        )
        if any(
            not isinstance(item, Mapping) for item in raw_candles
        ):
            raise MarketDataError("OANDA candle must be an object")
        return tuple(sorted(candles, key=lambda item: item.open_time))

    @staticmethod
    def _validate_response_semantics(
        payload: Mapping[str, Any], instrument: CurrencyPair, granularity: str
    ) -> None:
        response_instrument = payload.get("instrument")
        response_granularity = payload.get("granularity")
        if response_instrument != instrument.symbol or response_granularity != granularity:
            raise MarketDataError("OANDA response market semantics do not match request")

    @classmethod
    def _parse_candle(
        cls,
        item: Mapping[str, Any],
        instrument: CurrencyPair,
        granularity: str,
        price_basis: str,
    ) -> MarketCandle:
        midpoint = item.get("mid")
        if not isinstance(midpoint, Mapping):
            raise MarketDataError("OANDA complete candle has no midpoint prices")
        try:
            open_time = _parse_rfc3339(_required_text(item, "time"))
            prices = tuple(
                Decimal(_required_text(midpoint, key)) for key in ("o", "h", "l", "c")
            )
        except (InvalidOperation, ValueError) as error:
            raise MarketDataError("OANDA candle contains invalid time or price") from error
        return MarketCandle(
            source=OANDA_SOURCE,
            instrument=instrument,
            granularity=granularity,
            price_basis=price_basis,
            open_time=open_time,
            open=prices[0],
            high=prices[1],
            low=prices[2],
            close=prices[3],
            complete=True,
            market_data_version=OANDA_MARKET_DATA_VERSION,
        )


def _required_text(mapping: Mapping[str, Any], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"OANDA {key} must be a string")
    return value


def _parse_rfc3339(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("OANDA candle time must include timezone")
    return parsed.astimezone(UTC)


def _rfc3339(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("OANDA query time must include timezone")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
