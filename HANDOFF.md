# AI Handoff

この文書は会話の要約ではなく、別AIセッションが安全に指揮を引き継ぐための入口である。
現在値を重複保存すると汚染源になるため、live statusは必ずPhase GateとGitから取得する。

## Durable history

- baseline commit: `5255d036ca1375b1ebd6c6e2c5f887e712df3a37`
- M2-CのB1、B2、B3、B4、B5はすべてunit review承認済み。
- final attempt 1と2の指摘は修正済み。
- final attempt 3は、materializer返却値がwork itemの正確なRequestとstage時刻へ
  結び付いていないP2で`REQUEST_CHANGES`。判定はPhase Gateへ記録済み。
- attempt 3 correctionは、返却値のexact-contract検証後にRequest、Claim時刻、
  指定されたSELECTED materialization時刻をwork itemと比較し、AdoptionやB4より前に
  不一致を拒否する。対応する負例テストを含む。
- final attempt 4は、SELECTEDなのにwork itemがmaterialization時刻を`None`として
  identityへcommitしない経路をP2として拒否。判定はPhase Gateへ記録済みで、
  SELECTEDではnon-Noneの正確な時刻を必須化するcorrectionと負例を追加した。
- final attempt 5は、比較をoverrideした`str` subclassでWorkItem version/IDとSwap
  resolution IDのcontent-addressed検証を迂回できるP2を拒否。判定はPhase Gateへ記録済みで、
  identity/versionを比較前にexact `str`へ固定するcorrectionと敵対的負例を追加した。
  同じdefect classをM2-C config、Swap Evidence、Strategy result、authorization文字列境界にも
  横断適用した。
- final attempt 6は、NO_SELECTION／AMBIGUOUSでもwork itemがSELECTED用時刻を持てたP2を
  拒否。判定はPhase Gateへ記録済みで、selectedはnon-None exact時刻、non-selectedは
  必ず`None`という双方向bindingと両outcomeの負例を追加した。
- `TASK.md`、`HANDOFF.md`、`REVIEW.md`、`DECISIONS.md`は、このcorrection cycleで
  review対象へ追加された運用メタデータである。

## Resume protocol

1. `AGENTS.md`、この4文書、frozen M2-C files、`run-phase-loop` Skillを読む。
2. 次を実行し、会話上の申告ではなくlive stateを確認する。

   ```powershell
   $Repo = 'C:\Users\soref\OneDrive\ドキュメント\VSCode\FT_FXNewslab'
   Set-Location -LiteralPath $Repo
   python .agents\skills\run-phase-loop\scripts\phase_gate.py status --phase M2-C
   git status --short
   ```

3. `final_changes_requested`ならfull phase checksを実行し、新しいfinal bundleを作る。
4. `awaiting_final_review`なら、そのbundleだけを渡した新規5.6 Sol reviewerを作る。
5. `complete`なら`assert-complete`、文字コード検査、Git差分確認、commit状態を確認する。
6. reviewerは再利用しない。`.phase-runs`、frozen files、review bundleは手編集しない。

## Environment facts

- repository pathには日本語が含まれる。PowerShellでは`-LiteralPath`を使う。
- `.venv` launcherが日本語パスで失敗する場合は、依存が揃ったUnicode対応のsystem
  Pythonを使い、`PYTHONUTF8=1`と`PYTHONDONTWRITEBYTECODE=1`を設定する。
- repository textはUTF-8 BOMなし。非ASCIIを含みWindows PowerShell 5.1が直接実行する
  `.ps1`だけは必要に応じてUTF-8 BOMを使う。
- pushは未許可であり、実行しない。

## Evidence lookup

- exact status/history: `.phase-runs/M2-C/state.json`
- review attempts: `.phase-runs/M2-C/reviews/`
- latest check logs: `.phase-runs/M2-C/checks/`
- implementation rationale/progress: `docs/exec-plans/0006-production-strategy-and-paper-trading-operations.md`
- current file inventory: `git status --short`と`git diff --stat`
