# Active Task

## Fixed objective

Milestone 2-D (`ordinary close Portfolio/Risk path`) のscope、B分割、受入条件を
`run-phase-loop` に従って凍結する。今回は設計baselineのcommitとPhase Gate初期化までとし、
B1実装は開始しない。push、merge、deployは行わない。

## Authoritative scope

baseline commit後、次のfrozen filesがM2-Dの設計・受入条件の正本となり、Phase中は
変更してはならない。

- `docs/phases/M2-D.toml`
- `docs/phases/M2-D/spec.md`
- `docs/phases/M2-D/acceptance.md`

実行手順の正本は `.agents/skills/run-phase-loop/SKILL.md`、機械状態の正本は
`.phase-runs/M2-D/state.json` と `phase_gate.py status --phase M2-D` である。
この文書や会話履歴と矛盾する場合は、frozen filesとPhase Gateを優先する。

## Required completion for this task

- M2-C完了commitとclean worktreeを独立確認する。
- M2-Dの不足契約、数量authority、no-overclose競合境界を現行コードから調査する。
- 新規5.6 Sol設計reviewを1回行い、P1/P2相当の曖昧さを凍結前に解消する。
- M2-D manifest、spec、acceptanceとcross-session handoffをUTF-8 BOMなしで保存する。
- manifest/self-test、文字コード、Git差分を検証する。
- user許可済みのローカルdesign baseline commitとPhase Gate `init`まで行う。
- B1実装へ進まず、凍結した作業方針をuserへ説明して停止する。

## Non-goals

M2-Dの実装、既存accepted contractの変更、Paper/Broker/Execution、emergency
liquidation変更、scheduler、daemon、CLI、push、merge、deployは今回行わない。
