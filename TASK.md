# Active Task

## Fixed objective

Milestone 3 (`Paper Execution and Ledger`) を `run-phase-loop` に従って B1 から B5 まで
順に実装・検証・独立reviewし、`assert-complete` を通す。push、merge、deployは行わない。

## Authoritative scope

次のfrozen filesがM3の設計・受入条件の正本であり、Phase中は変更してはならない。

- `docs/phases/M3.toml`
- `docs/phases/M3/spec.md`
- `docs/phases/M3/acceptance.md`

実行手順の正本は `.agents/skills/run-phase-loop/SKILL.md`、機械状態の正本は
`.phase-runs/M3/state.json` と `phase_gate.py status --phase M3` である。
この文書や会話履歴と矛盾する場合は、frozen filesとPhase Gateを優先する。

## Current position

M3は2026-08-08に完了した。`phase_gate.py assert-complete --phase M3`が
`completion_verified: true`を返し、`approved_tree`が記録されている。B1〜B5すべてが
新規reviewerでAPPROVEされ（B4・B5はそれぞれ1回・2回のreject-fix-reサイクルを経た）、
final reviewも2回目でAPPROVE（1回目は`paper/store.py`のDecimal精度P0が見つかり修正）。
commitはまだ行っていない（working treeは未commitのまま、userの許可待ち）。

M2-Dは既にcommit・push済み（`a15c790..cadc25a`）。

## Required completion for this task

完了済み。残るのはuserの指示によるcommit判断のみ。次のMilestone（M4等）に着手する場合は
新しいdesign freeze・`init`から始める。

## Non-goals

frozen design/acceptanceの変更、既存accepted contractの変更、Broker/Execution実接続、
LIVE執行、scheduler、daemon、CLI、push、merge、deployは行わない。
