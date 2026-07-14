# ADR-0006: GMO FX Public KLines Are the Primary Forward Market Source

## Status

Accepted

## Context

Forward evaluation needs operationally available `USD_JPY` M1 OHLC evidence for up to
three days, explicit price semantics, safe candle completion, and immutable replay.
OANDA v20 satisfies the candle shape but API token availability is not established in
the user's environment.

GMO FX Public API was evaluated against the same contract. Its Public KLine endpoint is
available without credentials and provides `USD_JPY`, BID/ASK M1 OHLC, Unix-millisecond
open times, historical provider dates, and a response time from which M1 completion can
be conservatively derived.

## Decision

Use GMO FX Public `USD_JPY` M1 BID KLines as the Primary operational source:

```text
source = gmo-fx-public-v1
price_basis = bid
market_data_version = gmo-fx-kline-bid-v1
```

Retrieve a bounded set of provider dates and filter the normalized UTC range. A candle
is complete only when `responsetime >= openTime + 1 minute`.

Keep Forward domain, calculation, evidence, and persistence contracts independent of
the provider. Keep OANDA v20 midpoint as an optional experimental adapter with distinct
market semantics.

## Consequences

- Research market evidence needs no Broker private credential or account state.
- BID-based outcomes are not interchangeable with OANDA midpoint outcomes.
- ExecPlan 0004 must segment or explicitly combine source, market data version, price
  basis, granularity, projection version, and formula version.
- A three-day request uses deterministic provider-date splitting rather than a generic
  pagination framework.
- Provider corrections remain separate immutable candle revisions.

## Why not

Do not synthesize midpoint high/low by averaging BID and ASK extrema; the extrema may
come from different instants. Do not import the Swap Bot GMO Broker adapter or pass
private trading credentials into Research. Familiarity with a Broker is not evidence of
market-data suitability; the Public contract and live capability probe are the basis for
this decision.
