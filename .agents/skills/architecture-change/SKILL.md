---
name: architecture-change
description: Use for cross-layer refactors, fx_core contract changes, package boundary changes, or architectural migrations. Do not use for isolated bug fixes.
---

# Architecture Change

1. Read `AGENTS.md`.
2. Read `docs/README.md`.
3. Read `docs/01_ARCHITECTURE.md`, `docs/02_DOMAIN_MODEL.md`, and relevant ADRs.
4. Map current dependencies before editing.
5. For changes matching `PLANS.md` criteria, create or update an ExecPlan.
6. State the invariant that must survive the change.
7. Prefer migration seams and adapters over a big bang rewrite.
8. Add architecture-boundary tests when practical.
9. Update an ADR when the decision changes, not merely because files moved.
10. Verify no forbidden dependency was introduced.

Review comments and code comments must not narrate the implementation.
Use code comments only for Why not constraints.
