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
  確認済み。M2-Dはcommit済み・push済み（5 commit、`a15c790..cadc25a`）。
- M3はPaper Execution and Ledgerを対象とする。B1 pure domain contracts、B2 deterministic
  fill engine、B3 ledger/PnL/swap accrual/reconciliation domain、B4 atomic persistence
  (migration `0006`、`SQLitePaperStore`のT0〜T7)、B5 one-intent application compositionの
  5 unitで構成する。2026-08-07〜08、Claude runtimeで完了した。
- B4は1回reject。1st reviewerが指摘した「distinct concurrent Step resolutionがterminal
  claim 1件に収束するreal-threadテストの欠落」を修正し、2nd reviewerがAPPROVE。
- B5は2回reject、milestone中で最も深刻な欠陥が見つかったunit。
  (1) 1st reviewer: `_advance()`がStep ordinalを常に0固定にしており、`maximum_steps>1`の
  fill policyがStep 0より先に進めない（frozen spec 136-142行「T1-T4はapplication-service
  entry point経由でのみ到達可能」に矛盾）。read-only `current_step_ordinal()`をB4 storeへ
  追加し修正。
  (2) 修正後、orchestrating sessionが実際にpublic entry pointを2回連続callして自ら再現検証
  したところ、より深い欠陥を発見：T1(`accept_entry_order`等)を毎回のcallで再実行しており、
  `created_at`列比較により2回目のcallが異なるClock時刻だと`PaperPersistenceConflict`で
  crashする。read-only `hydrate_accepted_order`/`hydrate_created_step`を追加し、
  `paper_order_id`がcontent-addressed（`created_at`は識別子から除外）であることを利用して
  T1を安全にskipするよう修正。2nd reviewerがAPPROVE。
- final review 2回。1st final reviewerがP0を発見：`paper/store.py`が複数のDecimal集計を
  `decimal.localcontext(PAPER_EXACT_ARITHMETIC_V1)`の外（Pythonデフォルトcontext、
  prec=28、Inexact非trap）で実行しており、`paper/ledger.py`のrebuild関数（正しくexact
  contextを使用）と食い違い、未改竄アカウントがreconcileでMISMATCHEDと誤判定される。
  orchestrating sessionが実際にPython Decimal計算で再現し確認。6箇所を修正。修正中に
  同種の潜在バグ（`contracts.py`の`PaperFill.__post_init__`、未到達だが同じroot cause）
  も発見しuser判断で同時に修正。2nd final reviewerがAPPROVE、`assert-complete --phase M3`
  で`completion_verified: true`を確認済み。M3はcommit済み・push済み（6 commit、
  `607bc90..c3fb0c4`）。

## Resume protocol

1. `AGENTS.md`、この4文書、`run-phase-loop` Skillを読む。
2. 次を実行し、会話やHANDOFFの申告ではなくlive stateを確認する。

   ```powershell
   $Repo = 'C:\Users\soref\OneDrive\ドキュメント\VSCode\FT_FXNewslab'
   Set-Location -LiteralPath $Repo
   python .agents\skills\run-phase-loop\scripts\phase_gate.py status --phase M3
   git status --short
   ```

3. M3は`status: complete`、`completion_verified: true`。M3はClose済み。次のMilestoneに
   着手する場合は新しいdesign freeze・initから始める
4. 過去のreview履歴・修正内容は`.phase-runs/M3/reviews/`と本ファイルのDurable historyを
   参照する。各attemptは別の新規reviewerで審査済み。reviewerを再利用せず、`.phase-runs`、
   frozen files、review bundleを手編集しない。
5. B5・final reviewで見つかった欠陥はいずれも「reviewer/final reviewerの指摘を鵜呑みに
   せず、実際にコードを動かして／手計算で再現してから受け入れる」ことで早期に深掘りできた
   （逆に、最初の修正だけでは不十分だったケースが2件ある）。この姿勢は次のMilestoneでも
   継続する。

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

- M3 status/history: `.phase-runs/M3/state.json`
- M3 frozen design: `docs/phases/M3.toml`と`docs/phases/M3/`
- M2-D status/history: `.phase-runs/M2-D/state.json`
- M2-D frozen design: `docs/phases/M2-D.toml`と`docs/phases/M2-D/`
- M2-C historical evidence: `.phase-runs/M2-C/`
- rationale/progress: `docs/exec-plans/0006-production-strategy-and-paper-trading-operations.md`
- current tree: `git status --short`、`git diff --stat`、`git log -1`
