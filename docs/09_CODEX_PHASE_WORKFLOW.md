# Codex Phase Goal Workflow

This workflow executes one frozen milestone as ordered B units. A coordinator owns `/goal`, one
writer implements at a time, and every review attempt uses a new read-only reviewer thread.

## Roles

- Phase design: `phase_designer` (reasoning tier)
- Goal coordinator: select the working tier before starting `/goal`
- Normal implementation: `phase_implementer` (working tier)
- Escalated implementation: `phase_implementer_escalated` (reasoning tier)
- B review: a new `phase_reviewer` (working tier) for every attempt
- Final review: a new `phase_final_reviewer` (reasoning tier)

## Runtime model mapping

Roles are defined as tiers so a Phase can be resumed on either runtime. Both agent sets use the same
agent names and are maintained in parallel; select the column matching the runtime executing the
Goal.

| Role | Tier | Codex (`.codex/agents/*.toml`) | Claude (`.claude/agents/*.md`) |
| --- | --- | --- | --- |
| `phase_designer` | reasoning | `gpt-5.6-sol` high | Opus 5 |
| `phase_final_reviewer` | reasoning | `gpt-5.6-sol` high | Opus 5 |
| `phase_implementer_escalated` | reasoning | `gpt-5.6-terra` high | Opus 5 |
| `phase_reviewer` | working | `gpt-5.6-terra` medium | Sonnet 5 |
| `phase_implementer` | working | `gpt-5.6-luna` medium | Sonnet 5 |
| Goal coordinator | working | `gpt-5.6-terra` medium | Sonnet 5 |
| Mechanical only | light | `gpt-5.6-luna` low | Haiku 4.5 |

Phase manifests bind agents by name only, so switching runtime mid-Phase changes no frozen hash and
requires no gate operation. A Phase may start on one runtime and finish on the other. Keep both
columns current when either changes; do not delete the inactive one.

The coordinator must not run two writers concurrently. Reviewers are read-only. Subagents inherit
the parent turn's live permissions, so select the intended permission mode before starting.

## Prepare and freeze a milestone

1. Ask `phase_designer` to complete the ExecPlan and create a manifest from
   `docs/phases/phase.example.toml`.
2. Review the design result. Resolve every `NEEDS_DECISION` item.
3. Commit the design and manifest as a clean baseline. Pushing is optional.
4. Initialize the phase state:

   ```bash
   python .agents/skills/run-phase-loop/scripts/phase_gate.py init \
     --manifest docs/phases/M2.toml
   ```

Initialization records the exact HEAD and SHA-256 of the manifest, specification, and acceptance
files. Every frozen input must already be tracked at HEAD with identical content; ignored or merely
untracked design files are rejected. Later commands fail closed if any frozen file changes.

If a committed workflow or agent-policy correction is required after initialization but before B1,
leave the frozen milestone files unchanged and run `refresh-baseline` from a clean tree. The command
is allowed only while every unit is still pending and no check or review history exists:

```bash
python .agents/skills/run-phase-loop/scripts/phase_gate.py refresh-baseline \
  --phase M2 --reason "update phase workflow policy before B1"
```

The gate preserves both durable snapshots in an audited refresh history. Product changes, frozen
design changes, or any correction after B1 starts require the normal unit flow or a new phase state.

## Start the Goal

Select the working tier for the primary chat, then start:

```text
/goal Complete M2 from docs/phases/M2.toml. Use $run-phase-loop. Process B units in order. For
each review attempt spawn a new phase_reviewer and never send follow-up work to an earlier reviewer.
Use phase_implementer normally and phase_implementer_escalated when the gate requests escalation.
Do not complete the goal until phase_gate.py assert-complete succeeds.
```

The detailed state transition protocol lives in the Skill. `/goal` remains the persistent outcome,
not the state machine.

## Maximum-effort escalation

No checked-in agent runs above its assigned tier. The coordinator may launch one reasoning-tier pass
at maximum effort only after stating the triggering condition and evidence. At least one condition
must hold:

- the decision can enable LIVE or real-money orders, authenticated Private transport, or access to
  credentials or secrets;
- a durable-data migration is destructive or irreversible;
- one high-reasoning pass leaves a P0/P1 unresolved across multiple trust, authority, or persistence
  boundaries; or
- two consecutive final-review attempts reject the same root cause.

Repository size, test count, an ordinary additive migration, implementation difficulty, or a first
review rejection is not sufficient. `phase_implementer_escalated` remains a separate gate response
and does not itself authorize a maximum-effort pass.

## Cost policy

Attempt count, not model tier, dominates cost: every rejection replays implementation, checks, and a
full review over a larger diff. M2-C needed 16 unit-review attempts and 7 final-review attempts, and
its final-review diffs alone totalled about 1.8 MB.

- Pass agents a bundle path, not bundle contents. Never paste frozen text or diffs into a prompt.
- Require conclusions in agent reports; no pasted diffs or full test logs.
- Run one working-tier self-audit against the frozen acceptance before `prepare-final-review`.
- Keep unit diffs at the frozen minimum. Surface beyond the requirement is re-read by every later
  review in the phase.

## Evidence and recovery

Generated state and review bundles live under `.phase-runs/<phase>/`. Do not edit them by hand.
Each bundle contains frozen design snapshots, the exact base-to-Git-reviewable-tree diff, check logs,
and a reviewer request. State seals and complete bundle digests detect mutation. Check logs are
hashed at capture and revalidated before bundling. Durable synthetic baseline commits are anchored
under `refs/codex-phase-runs/`, so normal Git maintenance cannot prune an active phase baseline.
Diffs are captured as bytes with external diff and text-conversion drivers disabled.
Verdicts are bound to one bundle nonce and recorded with a unique reviewer thread ID. Only the
primary Goal coordinator runs gate commands; implementation agents never run the gate or write
evidence.

The recorded Git-reviewable tree includes tracked and nonignored untracked files. Git-ignored files
are outside the evidence boundary, so required fixtures or generated inputs must be moved to a
tracked or nonignored path. A configured check fails if it changes the Git-reviewable tree, even when
the command exits zero. Check names accept only ASCII letters, digits, underscores, and hyphens.
Windows device names such as `CON`, `NUL`, `COM1`, and `LPT1` are rejected.
Edits made before opening the first/next B unit or before initial phase checks are also rejected, so
work cannot disappear between reviewed B diffs.

Phase and B-unit names are validated as Windows-safe path components; unit uniqueness is
case-insensitive. Unit review bundles and final review bundles use separate directory namespaces.

If work stops, run:

```bash
python .agents/skills/run-phase-loop/scripts/phase_gate.py status --phase M2
python .agents/skills/run-phase-loop/scripts/phase_gate.py verify --phase M2
```

Resume from the returned state. Do not recreate or skip an approved unit. A repeated blocking review
automatically requests the escalated implementer once the manifest threshold is reached.
If interruption leaves a prepared bundle or matching nonce-bound verdict just before state was
saved, rerun the same gate command; it reconstructs or adopts that orphan evidence idempotently.
Failed phase checks enter `phase_checks_failed`; the gate selects the escalated implementer and
allows corrected phase checks to run again. Final-review corrections also always select escalation.

## Boundaries

- Design changes require a new clean design baseline and a new phase state.
- Commits, pushes, merges, deployments, credentials, and new permissions remain explicit actions.
- The native reviewer can still read the repository. For strict input isolation, start a separate
  Codex App Server thread with the generated bundle directory as its only readable workspace.
- Native mode trusts the primary coordinator to supply the real spawned reviewer thread ID. The
  local gate rejects reuse but cannot query the Codex thread registry by itself.
- State seals are tamper-evidence for the native cooperative workflow, not a hostile-process security
  boundary. Use an external App Server orchestrator when implementation code must be adversarially
  isolated from phase state.
- Never mark the Goal complete from prose alone. `assert-complete` is the terminal mechanical gate.
