---
name: run-phase-loop
description: Execute a frozen milestone as ordered B units with implementation, deterministic checks, a fresh independent reviewer for every attempt, correction loops, escalation, and a final milestone gate. Use when Codex is asked to run or resume a Phase/Milestone Goal from a docs/phases TOML manifest, automate implement-test-review-fix cycles, or prove a phase complete.
---

# Run Phase Loop

Use `scripts/phase_gate.py` as the authority for state, frozen hashes, check evidence, review attempts,
and completion. Never edit `.phase-runs/` by hand.

Only the primary Goal coordinator may execute gate transitions or record reviewer output. Never
delegate gate commands to an implementation agent.

## Start or resume

1. Read `AGENTS.md`, `PLANS.md`, the manifest, and every frozen file it names.
2. Run `phase_gate.py status --phase <M>`.
3. If no state exists, require a clean committed design baseline, then run
   `phase_gate.py init --manifest <path>`.
4. Run `phase_gate.py verify --phase <M>` before every state transition.
5. Follow the exact state returned by the gate. Do not skip units or infer approval.

Do not edit the Git-reviewable tree while the gate is waiting to open the first unit, the next unit,
or the initial phase checks. Such changes are rejected rather than absorbed into a later base.

Read `references/state-machine.md` when selecting the next command. Read
`references/reviewer-contract.md` before spawning or recording a reviewer.

## Execute one B unit

1. Run `start-unit --phase <M> --unit <B>`.
2. Spawn `phase_implementer` for the current unit. Give it the manifest, frozen files, current unit,
   current gate status, and repository instructions. Allow only this writer to edit.
3. If the gate status requests escalation, spawn `phase_implementer_escalated` instead.
4. Run `run-checks --phase <M> --scope unit`. Fix failures before review.
5. Run `prepare-review --phase <M>`.
6. Spawn a **new** `phase_reviewer`. Do not follow up with an earlier reviewer. Give it only the
   generated bundle path and require the reviewer contract.
7. Save the returned text verbatim outside the generated bundle, then run
   `record-review --phase <M> --thread-id <new-id> --verdict-file <path>`.
8. On `REQUEST_CHANGES`, return to step 1 for the same unit. On `APPROVE`, run
   `advance --phase <M>`.

Reviewer thread IDs are single-use. Never invent an ID or reuse a reviewer thread. Do not expose
earlier verdicts to a new reviewer.

## Complete the milestone

When `advance` enters `phase_review`:

1. Run `run-checks --phase <M> --scope phase`.
2. Run `prepare-final-review --phase <M>`.
3. Spawn a new `phase_final_reviewer` with only the final bundle.
4. Record it with `record-final-review`.
5. If changes are requested, fix them with the escalated implementer, rerun phase checks, and use a
   new final reviewer.
6. Run `assert-complete --phase <M>`.
7. Only after that succeeds may the parent mark `/goal` complete.

## Stop conditions

Pause and ask for direction when:

- a frozen file changes;
- design contradicts the repository or cannot satisfy an invariant;
- required permissions, credentials, or external systems are unavailable;
- a requested action would commit, push, merge, deploy, or broaden scope without authorization;
- the gate rejects state or evidence that cannot be reconstructed safely.

Do not weaken checks, rewrite evidence, edit the design, or convert a failed command into a summary.
