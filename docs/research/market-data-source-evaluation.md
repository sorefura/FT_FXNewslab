# Market Data Source Evaluation for ExecPlan 0003

## Purpose

ExecPlan 0003 requires reproducible `USD_JPY` M1 OHLC evidence from
`Signal.created_at` through five forward horizons, including the three-day path. This
review evaluates operational access separately from the provider-independent Forward
Result contract.

The existing OANDA adapter proves the `MarketDataSource` boundary against recorded
official-schema responses. It is not accepted as the Primary operational source because
API token availability has not been established in the user's environment.

## Required capabilities

| Requirement | GMO FX official contract | Public probe evidence | Assessment |
|---|---|---|---|
| Instrument | `symbol` is required; `USD_JPY` is listed in symbols | Public symbols returned `USD_JPY` | Supported |
| Instrument normalization | KLine request is scoped by required `symbol` | `USD_JPY` maps losslessly to `CurrencyPair(USD, JPY)` | Supported |
| M1 | `interval=1min` is supported with `YYYYMMDD` | M1 responses observed | Supported |
| Candle start timestamp | `openTime` is Unix milliseconds | Millisecond values normalized to UTC exactly | Supported |
| OHLC | Response specifies string `open/high/low/close` | Decimal-compatible strings observed for every returned candle | Supported |
| Historical path | M1 dates from 2023-10-28; one provider date per request | 2025-07-14 and 2026-07-10/13/14 returned data | Supported |
| Three-day range | No arbitrary from/to; date-based retrieval | A three-day UTC window is covered by bounded adjacent date requests | Supported with splitting |
| Response size | A date returns the date's candles; no pagination parameter | Up to 1,440 M1 candles observed in one response | Supported |
| Candle completion | No explicit `complete` field | `responsetime` and `openTime` show current in-progress M1 candle | Safely derivable |
| Price basis | Required `priceType` is `BID` or `ASK` | Separate BID and ASK OHLC responses observed | Supported, no midpoint |
| Authentication | Public API requires no authentication | Status, symbols, BID KLines, and ASK KLines fetched without credentials | Public |
| Rate/reliability | Public REST fixed rate is not separately stated; general load limiting and `ERR-5003` are documented | Five bounded sequential requests completed successfully | Suitable with bounded sequential GETs |
| Reproducibility | Date, basis, interval, and timestamp are explicit | Same content has deterministic identity; corrections can be separate revisions | Supported |

## Official contract evidence

The official GMO FX API documentation identifies:

- Public endpoint `https://forex-api.coin.z.com/public` and API version `v1`.
- Public API authentication is not required.
- `GET /public/v1/klines` requires `symbol`, `priceType`, `interval`, and `date`.
- `priceType` accepts `BID` or `ASK`.
- `1min` is available with `YYYYMMDD`; M1 dates are available from 2023-10-28.
- The provider trading date changes at the documented Japan-market boundary rather than
  accepting an arbitrary timestamp range.
- KLine fields are `openTime` Unix milliseconds and string OHLC.
- General load limiting may occur; `ERR-5003` represents an API-call limit and
  maintenance has separate error codes.

Primary source: [GMO Coin Foreign Exchange FX API documentation](https://api.coin.z.com/fxdocs/).

## Public capability probe

Probe date: 2026-07-14. No API key, account state, position, margin, or trading endpoint
was used.

Observed Public endpoints:

- `GET https://forex-api.coin.z.com/public/v1/status` returned status `OPEN`.
- `GET https://forex-api.coin.z.com/public/v1/symbols` included `USD_JPY`.
- `GET https://forex-api.coin.z.com/public/v1/klines` returned BID and ASK M1 OHLC.

Recorded non-secret summary:

| Provider date | BID M1 count | First/last UTC observation |
|---|---:|---|
| 2026-07-10 | 1,440 | 2026-07-09 21:00 / 2026-07-10 20:59 |
| 2026-07-11 | 0 | weekend |
| 2026-07-12 | 0 | weekend |
| 2026-07-13 | 1,380 | 2026-07-12 22:00 / 2026-07-13 20:59 |
| 2026-07-14 | partial current day | included the then-current open M1 candle |
| 2025-07-14 | 1,380 | historical response remained available |

The current-day response included a candle whose `openTime + 1 minute` was later than
the response's `responsetime`. Therefore response membership does not imply completion.
The safe rule is:

```text
complete = response_time >= open_time + granularity duration
```

The repository probe is:

```powershell
python tools/market_data_probe/gmo_fx.py `
  --start 2026-07-10T20:50:00Z `
  --end 2026-07-13T20:50:00Z `
  --pair USD_JPY `
  --basis BID
```

It emits summary counts and timestamps only and never creates a ForwardResult.

The recorded three-day command completed five deterministic Public requests, returned
1,380 complete candles in the requested UTC window, covered
`2026-07-10T20:50:00Z` through the expected exclusive-end predecessor
`2026-07-13T20:49:00Z`, and reported a maximum of 1,440 candles in one response. One
in-progress candle from the adjacent current-day response was detected and excluded.

## Price basis decision

GMO KLines expose BID and ASK separately and do not expose midpoint candles. Component
averaging of BID and ASK high/low would create extrema that may never have existed at
one timestamp, so it is prohibited.

The candidate production semantics are:

```text
source = gmo-fx-public-v1
price_basis = bid
market_data_version = gmo-fx-kline-bid-v1
```

This measures movement in the provider's BID KLine series. It is not trade PnL and is
not interchangeable with OANDA midpoint evidence. ExecPlan 0004 must not aggregate the
two market semantics without an explicit combined analysis.

## Suitability decision

GMO FX Public KLines satisfy all Primary Source acceptance conditions:

- operational Public access is available without a trading credential;
- USD_JPY M1 OHLC and UTC-normalizable provider timestamps exist;
- complete candles can be conservatively derived from provider response time;
- BID extrema are direct provider evidence and support reproducible MFE/MAE;
- the three-day path is available through bounded deterministic date requests;
- evidence identity and market semantics can remain versioned and append-only.

GMO FX is therefore selected as the Primary Market Data Source for ExecPlan 0003,
subject to the production adapter contract and explicit Public smoke passing. OANDA
remains an optional experimental adapter because its candle semantics are valid but its
operational token availability is not established.
