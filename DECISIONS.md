# Cross-session Decisions

ここには複数AIセッションで変えてはいけない作業上の決定だけを置く。製品設計の詳細は
frozen spec、ADR、ExecPlanを正本とし、この文書へコピーしない。

## Authority order

1. `AGENTS.md`と承認済みADR
2. frozen Phase design/acceptanceとmanifest
3. `run-phase-loop` SkillとPhase Gate state
4. current ExecPlan
5. この4つのhandoff文書
6. AIの会話履歴・自己申告

下位情報が上位情報と矛盾する場合は上位を採用し、黙って折衷しない。

## Workflow decisions

- M単位の設計・最終reviewは5.6 Sol、B単位の実装はLuna/Terra medium相当を基本とする。
- routineな調査、gate操作、文書同期、既知のmechanical correctionはLuna/Terra
  medium相当を優先し、5.6 Sol highはPhase設計、独立review、最終reviewへ限定する。
- reviewerは1 attemptにつき1回だけ起動する。App taskの状態取得失敗を理由に同じ仕事を
  重複起動せず、Git・Phase Gate・bundleを正本に再開する。
- rejection後は修正し、必ず別の新規reviewerで再審査する。
- review bundle作成後はlive treeを変更しない。変更が必要なら判定を正式記録して次attemptへ進む。
- live statusをMarkdownへ正本として複製しない。Phase GateとGitを毎回照会する。
- commitはユーザー許可済み、pushは未許可。

## Engineering decisions

- 外部/store境界のevidenceはexact typeを要求し、override可能なinstance validatorを信頼せず
  base implementationで検証する。
- content-addressed IDだけでなく、呼出し元のimmutable work itemと返却lineageを比較する。
- append-or-compare、immutable replay、corruption fail-closedを維持し、自動repairしない。
- 実統合テストはfakeだけで代替せず、実M2-B5 materializer、Live Adoption、B4 persistenceを通す。
- 日本語Windowsパスを常態とし、repository textはUTF-8 BOMなしにする。BOMはconsumer契約が
  必要とするPowerShell 5.1 scriptだけに限定する。
- `.phase-runs`、frozen files、prepared bundleを手作業で修正しない。
