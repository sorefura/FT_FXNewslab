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

- model方針はvendor名ではなくtierで定義する。M単位の設計・最終review・escalated実装は
  reasoning tier、B review・B実装・coordinatorはworking tier、gate操作と文書同期はlight tierを
  通常値とする。tierから実modelへの対応表は`docs/09_CODEX_PHASE_WORKFLOW.md`が正本である。
- 2026-08-06、GPTのクレジット制約により実行runtimeをClaudeへ切り替えた。Codex側の
  `.codex/agents/`とClaude側の`.claude/agents/`は同じagent名で並行維持し、どちらも削除しない。
  同一Phaseを途中でruntime間を跨いで再開してよい。phase manifestはagentを名前でしか参照しない
  ため、切替はfrozen fileのhashへ影響せず、gate操作も不要である。
- 契約密度の高い実装をlight tierへ落とさない。rejection往復が増えて逆に高くつく。
- maximum effortを常設agentの既定値にしない。次のいずれかを満たす場合だけ、coordinatorが
  条件と根拠を明示してreasoning tierを単発起動する。
  - LIVE/実資金order、authenticated Private transport、credential/secret accessを有効化し得る判断。
  - durable dataに対する破壊的または不可逆なmigration。
  - 通常passを1回使っても、複数のtrust・authority・persistence境界にまたがるP0/P1が未解決。
  - 同一root causeによりfinal reviewが2 attempt連続でrejectされた。
- repository規模、test件数、通常のadditive migration、実装難度、最初のreview rejectionだけでは
  maximum effort条件を満たさない。gateが要求する`phase_implementer_escalated`とは別に扱う。
- コストを支配するのはmodel階層ではなくrejectionの往復回数である。M2-Cはunit review 16 attempt、
  final review 7 attemptを要し、final reviewのdiffだけで約1.8MBに達した。往復を減らす。
- agentへはbundle pathだけを渡し、frozen本文・diff・ファイル内容を貼らない。
  agentの報告は結論のみとし、diffやtest全出力を貼らせない。
- `prepare-final-review`の前にworking tierでfrozen acceptanceに対するself-auditを1回挟む。
  reasoning tierのfinal review attemptはphase全体diffを読む最大の消費源である。
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
