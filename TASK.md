# Active Task

## Fixed objective

Milestone 2-D (`ordinary close Portfolio/Risk path`) を `run-phase-loop` に従って B1 から B5 まで
順に実装・検証・独立reviewし、`assert-complete` を通す。push、merge、deployは行わない。

## Authoritative scope

次のfrozen filesがM2-Dの設計・受入条件の正本であり、Phase中は変更してはならない。

- `docs/phases/M2-D.toml`
- `docs/phases/M2-D/spec.md`
- `docs/phases/M2-D/acceptance.md`

実行手順の正本は `.agents/skills/run-phase-loop/SKILL.md`、機械状態の正本は
`.phase-runs/M2-D/state.json` と `phase_gate.py status --phase M2-D` である。
この文書や会話履歴と矛盾する場合は、frozen filesとPhase Gateを優先する。

## Current position

M2-Dは2026-08-07に完了した。`phase_gate.py assert-complete --phase M2-D`が
`completion_verified: true`を返し、`approved_tree`が記録されている。B1〜B5すべてが
新規reviewerでAPPROVEされ、final reviewもAPPROVE。commitはまだ行っていない
（working treeは未commitのまま、userの許可待ち）。

## Required completion for this task

完了済み。残るのはuserの指示によるcommit判断のみ。

## Non-goals

frozen design/acceptanceの変更、既存accepted contractの変更、Paper/Broker/Execution、
emergency liquidation変更、scheduler、daemon、CLI、push、merge、deployは行わない。
