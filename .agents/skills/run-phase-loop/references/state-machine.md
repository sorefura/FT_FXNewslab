# Phase state machine

| State | Required next action |
| --- | --- |
| `ready_for_unit` | `start-unit` for the exact current unit |
| `implementing` | edit only the current unit, then `run-checks --scope unit` |
| `awaiting_review` | spawn a new reviewer, then `record-review` |
| `changes_requested` | `start-unit` for the same unit and correct findings |
| `unit_approved` | `advance` |
| `phase_review` | `run-checks --scope phase`, then `prepare-final-review` |
| `phase_checks_failed` | use the escalated implementer, correct the failure, rerun phase checks |
| `awaiting_final_review` | spawn a new final reviewer, then `record-final-review` |
| `final_changes_requested` | correct the phase, rerun phase checks, prepare a new final review |
| `complete` | `assert-complete`, then complete the Goal |

Every mutating command verifies the frozen manifest, specification, and acceptance hashes first.
The gate records one base commit per phase and per unit and anchors each commit under
`refs/codex-phase-runs/`, so normal Git pruning cannot remove an active baseline. Generated diffs
include the Git-reviewable tree: tracked and nonignored untracked files, excluding `.phase-runs/`
evidence. Git-ignored files are outside the evidence boundary; promote any required test input to a
tracked or nonignored path.

Unit checks select manifest entries whose scope is `unit` or `all`. Phase checks select `phase` or
`all`. Check names are restricted to path-safe identifiers. Every selected command must exit zero
without changing the Git-reviewable tree in the latest run before a bundle can be generated. Each
captured log is hashed immediately, rechecked after the run, and rechecked again before bundling.
Diff evidence is captured as raw bytes with external diff and text-conversion drivers disabled.

The gate seals the expected tree between `init` and the first unit, between approved units, and
between the last unit and initial phase checks. Edits in those gaps fail closed instead of being
absorbed into a later unit baseline.

The initial phase-check attempt consumes that transition seal. A failed attempt enters
`phase_checks_failed`, selects the escalated implementer, and permits correction plus rerun. Later
stale phase evidence can likewise be regenerated without reviving the one-time transition seal.

Preparing a review is retry-safe: an orphan attempt directory left before state persistence is
replaced from the sealed source and check evidence. Recording a review is also retry-safe only when
an orphan `verdict.md` exactly matches the supplied nonce-bound verdict; mismatches fail closed.

Phase and unit names must be Windows-safe path components and units must also be unique after
case-folding. Unit reviews live under `reviews/units/<unit>/`; final reviews use `reviews/final/`, so
a valid unit name can never overlap the final-review namespace.

After the configured number of rejected unit reviews, the gate returns
`escalation_required: true`. Use the manifest's escalated implementer for the next correction.
