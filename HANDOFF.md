# AI Handoff

この文書は会話の要約ではなく、別AIセッションが安全に指揮を引き継ぐための入口である。
live statusを重複保存すると汚染源になるため、Phase GateとGitを必ず照会する。

## Durable history

- M2-C baseline: `5255d036ca1375b1ebd6c6e2c5f887e712df3a37`。
- M2-C complete commit: `a15c790abbfa18f5731febb9f6c67e1c2498e845`。
- M2-CはB1〜B5、全unit review、final review、full checks、Phase Gate
  `assert-complete`まで完了した。pushは行っていない。
- M2-Dはordinary close専用のStrategy evaluation、Portfolio quantity allocation、
  Risk reduce-only/no-overclose、`ApprovedCloseIntent` evidenceまでを対象とする。
- 現行M2-A exit evidenceにはquantityとtyped terminal acquisition outcomeがなく、
  legacy Portfolio/Risk型はentry Candidate専用である。この不足はaccepted型を変更せず、
  additive operational envelope/capacity/close-specific decision型で補う。
- 新規設計reviewは、opaque IDの流用、legacy decision再利用、非原子的な
  reservationをblocker候補として指摘した。凍結specはこれらを禁止した。
- 2026-08-06、GPTのクレジット制約により実行runtimeをClaudeへ切り替えた。Codex側agentは削除せず
  並行維持し、runtimeに応じて選択する。frozen spec/acceptanceは凍結のまま維持する判断をuserが
  下した。scopeは変更しない。
- 2026-08-06〜07、M2-DのB1〜B5をClaude runtimeで完了した。各unitとも新規reviewerが実装欠陥を
  検出し、修正後の再審査でAPPROVEした。B1(stale signal誤reversal、forged lineage検証の迂回、
  architecture tripwire欠落)、B2(Risk側reservation snapshot lineage未検証)、
  B3(config/Swap Evidence改竄テスト欠落、`.tmp_pytest/`未gitignoreによる31ファイル混入事故)、
  B4(P0: capacityが実際にこのCandidateのwork itemに紐づくか未検証という実害ある欠陥)、
  B5(Portfolio REDUCE経路と不整合reservationの防御テスト欠落)がそれぞれ見つかった。
  final review前のself-auditで、living documentation(README等)がB1止まりで更新されて
  いなかった実質的ギャップと、architecture tripwireが`ordinary_close_store.py`/
  `ordinary_close_application.py`を未スキャンだったギャップを発見し修正した。
  final reviewはAPPROVE。`assert-complete --phase M2-D`で`completion_verified: true`を
  確認済み。commitはまだ行っていない（userの許可待ち）。

## Resume protocol

1. `AGENTS.md`、この4文書、`run-phase-loop` Skillを読む。
2. 次を実行し、会話やHANDOFFの申告ではなくlive stateを確認する。

   ```powershell
   $Repo = 'C:\Users\soref\OneDrive\ドキュメント\VSCode\FT_FXNewslab'
   Set-Location -LiteralPath $Repo
   python .agents\skills\run-phase-loop\scripts\phase_gate.py status --phase M2-D
   git status --short
   ```

3. M2-Dは`status: complete`、`completion_verified: true`。次のアクションはuserの指示による
   commit判断のみ。次のMilestoneに着手する場合は新しいdesign freeze・`init`から始める。
4. 過去のreview履歴・修正内容は`.phase-runs/M2-D/reviews/`と本ファイルのDurable historyを
   参照する。各attemptは別の新規reviewerで審査済み。reviewerを再利用せず、`.phase-runs`、
   frozen files、review bundleを手編集しない。

## Model and cost policy

- 設計・最終review・escalated実装はreasoning tier。B review、B実装、coordinatorはworking tier。
  gate操作と文書同期はlight tierでよい。
- tierから実modelへの対応表は`docs/09_CODEX_PHASE_WORKFLOW.md`が正本。Codexは`.codex/agents/`、
  Claudeは`.claude/agents/`を使う。両方を維持し、どちらも削除しない。
- phase manifestはagentを名前でしか参照しないため、runtimeをPhase途中で跨いでもfrozen hashへ
  影響せず、gate操作も不要である。同一Phaseを別runtimeで再開してよい。
- 契約密度の高い実装をlight tierへ落とさない。rejection往復が増えて逆に高くつく。
- コストを支配するのはmodel階層ではなくrejectionの往復回数である。M2-Cはunit review
  16 attempt、final review 7 attemptを要した。
- agentへはbundle pathだけを渡す。frozen本文、diff、ファイル内容をプロンプトへ貼らない。
- agentの報告は結論のみとする。diffやtest全出力を貼らせない。
- `prepare-final-review`の前にworking tierでfrozen acceptanceに対するself-auditを1回挟む。

## Implementation posture

frozen unitのscopeは成果物であると同時に上限である。

- frozen acceptanceが要求しない型、field、引数、helper、設定点、防御分岐を足さない。
- frozen契約が既に排除している状態へのguardを書かない。
- 仕様の再説明をdocstringやコメントへ置かない。コメントはWhy notに限る。
- 新規moduleより既存moduleへの追加を優先する。unit scope外のファイルへ触らない。
- 実装surfaceを増やすと、そのPhaseの以降の全reviewがそれを読み直す分だけ高くつく。
- テストはこの制限の対象外とする。frozen unitが列挙するケースは全て網羅する。

## Environment facts

- repository pathには日本語が含まれる。PowerShellでは`-LiteralPath`を使う。
- `.venv` launcherが日本語パスで失敗する場合は、依存が揃ったUnicode対応system
  Pythonを使い、`PYTHONUTF8=1`と`PYTHONDONTWRITEBYTECODE=1`を設定する。
- sandboxが`%TEMP%`を読めない環境では`pytest`のtmp_path fixtureが大量のPermissionErrorを出す。
  これはテスト失敗ではない。書き込み可能な`--basetemp`を渡して切り分ける。
- repository textはUTF-8 BOMなし。非ASCIIを含みWindows PowerShell 5.1が直接実行する
  `.ps1`だけはconsumer契約に応じてUTF-8 BOMを使う。
- commitは許可済み。push、merge、deployは未許可。

## Evidence lookup

- M2-D status/history: `.phase-runs/M2-D/state.json`
- M2-D frozen design: `docs/phases/M2-D.toml`と`docs/phases/M2-D/`
- M2-C historical evidence: `.phase-runs/M2-C/`
- rationale/progress: `docs/exec-plans/0006-production-strategy-and-paper-trading-operations.md`
- current tree: `git status --short`、`git diff --stat`、`git log -1`
