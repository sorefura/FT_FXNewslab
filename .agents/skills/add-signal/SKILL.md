---
name: add-signal
description: Use when adding or changing Observation-to-Feature-to-Signal logic, news scoring, currency scoring, pair transformation, signal horizons, or scorer versions.
---

# Add or Change a Signal

1. Read `docs/02_DOMAIN_MODEL.md`.
2. Read `docs/03_SIGNAL_AND_RESEARCH.md`.
3. Read `docs/05_DATA_AND_VERSIONING.md`.
4. Identify the target: Currency or Pair.
5. Define Signal type and Horizon semantics.
6. Identify source Feature types.
7. Define direction, strength, and confidence meanings independently.
8. Assign producer/scorer/transformation versions as applicable.
9. Preserve source Feature lineage.
10. Never derive evaluation metrics using future information.
11. Add tests that state What the Signal guarantees.
12. Add or update Forward evaluation slices for the new Signal type.
13. Do not connect a new Signal directly to Execution.
14. Strategy adoption is a separate change and requires an explicit policy.

When changing Signal meaning, do not reuse the previous scorer version.
