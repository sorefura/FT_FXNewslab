# ADR-0005: SQLite Reference Store Is Separate from fx_core

## Status

Accepted

## Context

Research and Live need to read the same immutable Observation, Feature, Signal, and lineage
records. Database code cannot enter `fx_core`, and neither application may import the other.

## Decision

Define records and repository contracts in `fx_core`. Provide numbered SQLite migrations and
the reference adapter in a separate `fx_signal_store` package that depends only on `fx_core`.

Live-specific decision persistence remains owned by the Live application.

## Consequences

- Research can consume stored Signals without importing Live modules.
- `fx_core` remains independent of SQLite and persistence frameworks.
- A future store may replace SQLite without changing the shared domain contract.

## Why not

Do not place the SQLite adapter in either application; that would make its sibling depend on
application-owned infrastructure or duplicate the canonical schema.

