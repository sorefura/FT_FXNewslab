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
- 新規5.6 Sol設計reviewは、opaque IDの流用、legacy decision再利用、非原子的な
  reservationをblocker候補として指摘した。凍結specはこれらを禁止した。

## Resume protocol

1. `AGENTS.md`、この4文書、M2-D frozen files、`run-phase-loop` Skillを読む。
2. 次を実行し、会話やHANDOFFの申告ではなくlive stateを確認する。

   ```powershell
   $Repo = 'C:\Users\soref\OneDrive\ドキュメント\VSCode\FT_FXNewslab'
   Set-Location -LiteralPath $Repo
   python .agents\skills\run-phase-loop\scripts\phase_gate.py status --phase M2-D
   git status --short
   ```

3. 現在のTASKは設計凍結で停止する。userから実装再開指示があるまでB1を開始しない。
4. 再開後はB1から順番に1 unitずつ実装し、各attemptを別の新規reviewerで審査する。
5. reviewerを再利用せず、`.phase-runs`、frozen files、review bundleを手編集しない。

## Environment facts

- repository pathには日本語が含まれる。PowerShellでは`-LiteralPath`を使う。
- `.venv` launcherが日本語パスで失敗する場合は、依存が揃ったUnicode対応system
  Pythonを使い、`PYTHONUTF8=1`と`PYTHONDONTWRITEBYTECODE=1`を設定する。
- repository textはUTF-8 BOMなし。非ASCIIを含みWindows PowerShell 5.1が直接実行する
  `.ps1`だけはconsumer契約に応じてUTF-8 BOMを使う。
- commitは許可済み。push、merge、deployは未許可。

## Evidence lookup

- M2-D status/history: `.phase-runs/M2-D/state.json`
- M2-D frozen design: `docs/phases/M2-D.toml`と`docs/phases/M2-D/`
- M2-C historical evidence: `.phase-runs/M2-C/`
- rationale/progress: `docs/exec-plans/0006-production-strategy-and-paper-trading-operations.md`
- current tree: `git status --short`、`git diff --stat`、`git log -1`
