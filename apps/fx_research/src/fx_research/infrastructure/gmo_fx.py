import json
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from fx_core import CurrencyPair
from fx_core.time import require_utc

from ..errors import MarketDataError
from ..forward import MarketCandle

GMO_FX_SOURCE = "gmo-fx-public-v1"
GMO_FX_MARKET_DATA_VERSION = "gmo-fx-kline-bid-v1"
GMO_FX_PUBLIC_BASE_URL = "https://forex-api.coin.z.com/public"
_JST = timezone(timedelta(hours=9))
_MAXIMUM_RANGE = timedelta(days=4)


class GmoFxTransport(Protocol):
    def get(self, url: str, *, timeout_seconds: float) -> Mapping[str, Any]: ...


class UrllibGmoFxTransport:
    def get(self, url: str, *, timeout_seconds: float) -> Mapping[str, Any]:
        request = Request(
            url,
            headers={"User-Agent": "FT-FXNewslab/0.1 forward-evaluation"},
            method="GET",
        )
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            raise MarketDataError(f"GMO FX API returned HTTP {error.code}") from error
        except (TimeoutError, URLError, OSError) as error:
            raise MarketDataError(
                f"GMO FX API request failed: {type(error).__name__}"
            ) from error
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise MarketDataError("GMO FX API returned malformed JSON") from error
        if not isinstance(payload, Mapping):
            raise MarketDataError("GMO FX API response must be an object")
        return payload


class GmoFxMarketDataSource:
    source = GMO_FX_SOURCE
    market_data_version = GMO_FX_MARKET_DATA_VERSION
    granularity = "M1"
    price_basis = "bid"

    def __init__(
        self,
        transport: GmoFxTransport,
        *,
        base_url: str = GMO_FX_PUBLIC_BASE_URL,
        timeout_seconds: float = 10.0,
    ) -> None:
        if not base_url.strip():
            raise ValueError("GMO_FX_PUBLIC_API_BASE_URL must not be blank")
        if timeout_seconds <= 0:
            raise ValueError("GMO_FX_API_TIMEOUT_SECONDS must be positive")
        self._transport = transport
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._response_cache: dict[
            tuple[CurrencyPair, str, str, str], tuple[MarketCandle, ...]
        ] = {}

    def fetch_candles(
        self,
        *,
        instrument: CurrencyPair,
        granularity: str,
        price_basis: str,
        start_at: datetime,
        end_at: datetime,
    ) -> tuple[MarketCandle, ...]:
        require_utc(start_at, "GMO FX candle start_at")
        require_utc(end_at, "GMO FX candle end_at")
        if instrument != CurrencyPair.parse("USD_JPY"):
            raise MarketDataError(f"unsupported GMO FX instrument: {instrument.symbol}")
        if granularity != self.granularity or price_basis != self.price_basis:
            raise MarketDataError("GMO FX forward evaluation requires M1 BID candles")
        if end_at <= start_at or end_at - start_at > _MAXIMUM_RANGE:
            raise MarketDataError("GMO FX candle range must be positive and at most four days")

        revisions: dict[str, MarketCandle] = {}
        for provider_date in _provider_dates(start_at, end_at):
            cache_key = (instrument, price_basis, granularity, provider_date)
            for candle in self._provider_date_candles(
                cache_key=cache_key,
                instrument=instrument,
                provider_date=provider_date,
            ):
                if start_at <= candle.open_time < end_at:
                    revisions[candle.revision_id] = candle
        return tuple(
            sorted(
                revisions.values(),
                key=lambda item: (item.open_time, item.revision_id),
            )
        )

    def _provider_date_candles(
        self,
        *,
        cache_key: tuple[CurrencyPair, str, str, str],
        instrument: CurrencyPair,
        provider_date: str,
    ) -> tuple[MarketCandle, ...]:
        cached = self._response_cache.get(cache_key)
        if cached is not None:
            return cached
        payload = self._transport.get(
            self._url(instrument, provider_date),
            timeout_seconds=self._timeout_seconds,
        )
        candles = self._candles(payload, instrument)
        self._response_cache[cache_key] = candles
        return candles

    def _url(self, instrument: CurrencyPair, provider_date: str) -> str:
        query = urlencode(
            {
                "symbol": instrument.symbol,
                "priceType": "BID",
                "interval": "1min",
                "date": provider_date,
            }
        )
        return f"{self._base_url}/v1/klines?{query}"

    def _candles(
        self, payload: Mapping[str, Any], instrument: CurrencyPair
    ) -> tuple[MarketCandle, ...]:
        if payload.get("status") != 0:
            raise MarketDataError("GMO FX KLine response reported failure")
        raw_candles = payload.get("data")
        if not isinstance(raw_candles, list):
            raise MarketDataError("GMO FX KLine response has no data array")
        try:
            response_time = _rfc3339(_required_text(payload, "responsetime"))
        except ValueError as error:
            raise MarketDataError("GMO FX response time is invalid") from error
        candles = []
        for item in raw_candles:
            if not isinstance(item, Mapping):
                raise MarketDataError("GMO FX candle must be an object")
            candle = self._candle(item, instrument, response_time)
            if candle is not None:
                candles.append(candle)
        return tuple(candles)

    def _candle(
        self,
        item: Mapping[str, Any],
        instrument: CurrencyPair,
        response_time: datetime,
    ) -> MarketCandle | None:
        try:
            open_time = datetime.fromtimestamp(
                int(_required_text(item, "openTime")) / 1000, tz=UTC
            )
            prices = tuple(
                Decimal(_required_text(item, key))
                for key in ("open", "high", "low", "close")
            )
        except (InvalidOperation, ValueError, OSError) as error:
            raise MarketDataError(
                "GMO FX candle contains invalid timestamp or price"
            ) from error
        if response_time < open_time + timedelta(minutes=1):
            return None
        return MarketCandle(
            source=self.source,
            instrument=instrument,
            granularity=self.granularity,
            price_basis=self.price_basis,
            open_time=open_time,
            open=prices[0],
            high=prices[1],
            low=prices[2],
            close=prices[3],
            complete=True,
            market_data_version=self.market_data_version,
        )


def _provider_dates(start_at: datetime, end_at: datetime) -> tuple[str, ...]:
    first = start_at.astimezone(_JST).date() - timedelta(days=1)
    last = end_at.astimezone(_JST).date()
    return tuple(item.strftime("%Y%m%d") for item in _dates(first, last))


def _dates(first: date, last: date) -> tuple[date, ...]:
    return tuple(
        first + timedelta(days=offset) for offset in range((last - first).days + 1)
    )


def _required_text(mapping: Mapping[str, Any], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"GMO FX {key} must be a string")
    return value


def _rfc3339(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("GMO FX response time must include timezone")
    return parsed.astimezone(UTC)
