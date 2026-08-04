# Codex Phase Goal Workflow

This workflow executes one frozen milestone as ordered B units. A coordinator owns `/goal`, one
writer implements at a time, and every review attempt uses a new read-only reviewer thread.

## Roles

- Phase design: `phase_designer` (`gpt-5.6-sol`, xhigh)
- Goal coordinator: select `gpt-5.6-terra`, medium before starting `/goal`
- Normal implementation: `phase_implementer` (`gpt-5.6-luna`, medium)
- Escalated implementation: `phase_implementer_escalated` (`gpt-5.6-terra`, high)
- B review: a new `phase_reviewer` (`gpt-5.6-sol`, high) for every attempt
- Final review: a new `phase_final_reviewer` (`gpt-5.6-sol`, xhigh)

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

## Start the Goal

Select Terra with medium reasoning for the primary chat, then start:

```text
/goal Complete M2 from docs/phases/M2.toml. Use $run-phase-loop. Process B units in order. For
each review attempt spawn a new phase_reviewer and never send follow-up work to an earlier reviewer.
Use phase_implementer normally and phase_implementer_escalated when the gate requests escalation.
Do not complete the goal until phase_gate.py assert-complete succeeds.
```

The detailed state transition protocol lives in the Skill. `/goal` remains the persistent outcome,
not the state machine.

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
