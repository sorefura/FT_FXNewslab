import argparse
import json
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen

JST = timezone(timedelta(hours=9))


def main() -> int:
    parser = argparse.ArgumentParser(prog="gmo-fx-market-data-probe")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--pair", default="USD_JPY")
    parser.add_argument("--basis", choices=("BID", "ASK"), default="BID")
    parser.add_argument(
        "--base-url", default="https://forex-api.coin.z.com/public"
    )
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args()
    start = _utc_datetime(args.start)
    end = _utc_datetime(args.end)
    if end <= start:
        parser.error("--end must be after --start")
    dates = _provider_dates(start, end)
    responses = [
        _fetch_date(
            base_url=args.base_url,
            pair=args.pair,
            basis=args.basis,
            provider_date=item,
            timeout_seconds=args.timeout,
        )
        for item in dates
    ]
    revisions: dict[tuple[str, str, str, str, str], datetime] = {}
    response_counts: dict[str, int] = {}
    incomplete = 0
    for provider_date, payload in zip(dates, responses, strict=True):
        response_time = _utc_datetime(_text(payload, "responsetime"))
        items = payload.get("data")
        if not isinstance(items, list):
            raise ValueError("GMO FX response data must be an array")
        response_counts[provider_date] = len(items)
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("GMO FX candle must be an object")
            open_time, identity = _candle_identity(item)
            if open_time + timedelta(minutes=1) > response_time:
                incomplete += 1
                continue
            if start <= open_time < end:
                revisions[identity] = open_time
    ordered = sorted(revisions.values())
    duplicates_by_time = len(ordered) - len(set(ordered))
    output = {
        "authentication": "PUBLIC",
        "candle_count": len(ordered),
        "changed_revisions_at_same_time": duplicates_by_time,
        "complete_semantics": "responsetime>=openTime+1min",
        "first_open_time": ordered[0].isoformat() if ordered else None,
        "historical_request_count": len(dates),
        "incomplete_candles_excluded": incomplete,
        "instrument": args.pair,
        "interval": "1min",
        "last_open_time": ordered[-1].isoformat() if ordered else None,
        "max_candles_in_one_response": max(response_counts.values(), default=0),
        "price_basis": args.basis.lower(),
        "provider_date_counts": response_counts,
        "provider_dates": dates,
        "requested_end": end.isoformat(),
        "requested_start": start.isoformat(),
    }
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _provider_dates(start: datetime, end: datetime) -> list[str]:
    first = start.astimezone(JST).date() - timedelta(days=1)
    last = end.astimezone(JST).date()
    return [item.strftime("%Y%m%d") for item in _dates(first, last)]


def _dates(first: date, last: date) -> list[date]:
    count = (last - first).days + 1
    return [first + timedelta(days=offset) for offset in range(count)]


def _fetch_date(
    *,
    base_url: str,
    pair: str,
    basis: str,
    provider_date: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    query = urlencode(
        {
            "symbol": pair,
            "priceType": basis,
            "interval": "1min",
            "date": provider_date,
        }
    )
    url = f"{base_url.rstrip('/')}/v1/klines?{query}"
    with urlopen(url, timeout=timeout_seconds) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict) or payload.get("status") != 0:
        raise ValueError("GMO FX KLine request did not succeed")
    return payload


def _candle_identity(item: dict[str, Any]) -> tuple[datetime, tuple[str, str, str, str, str]]:
    timestamp = _text(item, "openTime")
    try:
        open_time = datetime.fromtimestamp(int(timestamp) / 1000, tz=UTC)
        prices = tuple(_text(item, key) for key in ("open", "high", "low", "close"))
        decimal_prices = tuple(Decimal(value) for value in prices)
    except (InvalidOperation, ValueError) as error:
        raise ValueError("GMO FX candle has invalid timestamp or OHLC") from error
    if min(decimal_prices) <= 0:
        raise ValueError("GMO FX candle prices must be positive")
    return open_time, (timestamp, prices[0], prices[1], prices[2], prices[3])


def _text(mapping: dict[str, Any], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"GMO FX {key} must be a string")
    return value


def _utc_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include timezone")
    return parsed.astimezone(UTC)


if __name__ == "__main__":
    raise SystemExit(main())
