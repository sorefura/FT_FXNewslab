# ADR 0007: Validated Research Evidence Requires Explicit Live Adoption

## Status

Accepted on 2026-07-15.

## Context

Research can derive a `VALIDATED_FOR_RESEARCH` assessment from one exact Evaluation
Report, Evaluation Run, input snapshot, and versioned Research validation policy.
That result establishes reproducible Research evidence. It does not answer which
Strategy may consume the Signal, for what period, or in which runtime mode.

Allowing Live to query the latest Research assessment at runtime would couple two
sibling applications, make authority change when Research data changes, and make an
old decision difficult to replay. Treating the Research enum as order authority would
also bypass Strategy, Portfolio, and Risk.

## Decision

Live adopts Research evidence only through an explicit operator decision.

At approval time, a Live-owned port reads one assessment by explicit ID from Research
SQLite in read-only mode and in one read transaction. It validates the complete
assessment/report/run/policy/input-snapshot lineage and copies canonical payloads and
hashes into an immutable Live evidence snapshot.

The operator supplies an immutable, exact-match, time-bounded
`StrategyAdoptionPolicy`. An explicit `--apply` atomically appends the evidence
snapshot, policy, and `APPROVED_FOR_STRATEGY` decision. Revocation appends a separate
decision referencing the approval. Dry-run is the default.

Runtime reads only Live records. It grants a Signal authorization only when exactly
one current, unrevoked decision matches Strategy identity, Signal target/type/horizon,
all nullable semantic versions, validity period, and runtime mode. Signal creation
or authorization before `max(approval.effective_from, approval.decided_at)` is not
eligible. The strict Candidate persistence boundary rechecks the same authority start.
The authorization is immutable lineage and must accompany the Signal through the new
strict Candidate persistence path.

The Live persistence boundary reconstructs each approval from its exact evidence
snapshot and adoption policy, and each revocation from its persisted approval, before
any append. Application-service validation alone is not treated as an integrity
boundary.

The evidence snapshot ID is reconstructed from its contract version, Research lineage
IDs, Research policy identity, status, cohort and metric hashes, condition-results
hash, and Evaluation input-snapshot identity. Payload/hash pairs and supported
versions are verified before the approval transaction. Research-side creation times
and Live `imported_at` remain first-write audit metadata rather than semantic snapshot
identity.

Approval semantic identity is the approval type plus exact evidence and adoption
policy identities. Revocation semantic identity is the revocation type plus the exact
approval ID. Decision time, actor, and reason are first-write audit metadata. A retry
with the same semantic identity and authority content reuses the persisted decision;
different authority content is a conflict. Consequently, retries do not move
`authority_start` to a later command time.

`APPROVED_FOR_STRATEGY` is not an Execution approval. Portfolio and Risk remain
mandatory and Broker submission remains disabled for this rollout.

## Consequences

- Research and Live remain sibling applications with no package import dependency.
- Runtime operation is stable if the Research DB is unavailable or later receives new
  assessments.
- Every adopted Signal and Candidate can be traced to exact Research evidence,
  adoption policy, operator, reason, and time window.
- Exact matching is intentionally operationally strict. New model, prompt, scorer,
  transformation, target, horizon, or Strategy versions require a new explicit
  adoption decision.
- A duplicate or ambiguous approval fails closed instead of selecting the latest.
- The first Live migration is additive because the pre-existing Live tables have no
  numbered migration history.
