# FXNewslab Program

The program follows one responsibility chain:

```text
Research discovers.
Signal describes.
Strategy chooses.
Portfolio allocates.
Risk permits.
Execution performs.
```

Implementation is divided into independently verifiable execution plans:

1. Shared Domain Foundation and Live Boundary Migration
2. Operational News Ingestion and Feature Production
3. Forward Signal Evaluation
4. Signal Validation Framework
5. Validated Signal Live Adoption

ExecPlan 0001 establishes the shared Signal language and verifies the downstream Live
boundaries without enabling orders. Operational collection and Research evaluation remain
separate plans so that provider operations and future observations cannot alter the ex-ante
Signal contract.

ExecPlan 0002 operationalizes News collection and Feature production. ExecPlan 0003 observes
each immutable Signal at five fixed forward horizons and stores exact market evidence plus a
replayable result. ExecPlan 0004 groups completed outcomes by strict semantic cohorts, stores
versioned aggregate metrics, and permits Research validation only through an explicit immutable
policy. Strategy adoption and Live authority remain ExecPlan 0005 responsibilities.

ExecPlan 0005 preserves three distinct authorities:

```text
VALIDATED_FOR_RESEARCH != APPROVED_FOR_STRATEGY != ORDER_APPROVED
```

Live imports one explicitly selected Research assessment as immutable evidence, then
requires a Live-owned, exact-match, time-bounded, revocable operator decision before a
Signal may reach Strategy. Runtime uses only copied Live evidence and decision state.
Strategy adoption never bypasses Portfolio, Risk, approved intent, Execution arming,
or idempotency, and ExecPlan 0005 keeps Broker submission disabled.
