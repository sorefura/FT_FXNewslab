# Active Task

## Fixed objective

Milestone 2-C (`concrete NewsFilteredCarryStrategy and persistence`) を
`run-phase-loop` に従って完了し、検証済みの変更をローカルcommitとして保存する。
push、merge、deployは行わない。

## Authoritative scope

次のfrozen filesが今回の設計・受入条件の正本であり、変更してはならない。

- `docs/phases/M2-C.toml`
- `docs/phases/M2-C/spec.md`
- `docs/phases/M2-C/acceptance.md`

実行手順の正本は `.agents/skills/run-phase-loop/SKILL.md`、機械状態の正本は
`.phase-runs/M2-C/state.json` と `phase_gate.py status --phase M2-C` である。
この文書や会話履歴と矛盾する場合は、frozen filesとPhase Gateを優先する。

## Required completion

- B1からB5を順番に実装・検査し、各attemptを別のreviewer identityで審査する。
- final reviewも新規の5.6 Sol reviewerが、生成bundleだけを審査する。
- `phase_gate.py assert-complete --phase M2-C`を成功させる。
- repository textがUTF-8 BOMなしで、日本語を含むWindowsパスから読めることを確認する。
- full pytest、Ruff、strict mypy、`git diff --check`を成功させる。
- user許可済みのローカルcommitまで行い、pushしない。

## Non-goals

M2-D、Portfolio、Risk、Execution、Paper/Broker連携、scheduler、daemon、CLI、
外部Swap provider、production default configは今回追加しない。
