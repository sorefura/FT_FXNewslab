# Independent Review Standard

Codex、実装agent、HANDOFFの「完了」「テスト成功」という申告は証拠として扱わない。
reviewerはfrozen design、生成されたimmutable bundle、差分、実ログだけから独立に判定する。

## Identity and evidence rules

- attemptごとに新しいreviewer threadを作り、過去のreviewerへfollow-upしない。
- reviewerは原則read-onlyとし、live repositoryや過去会話を渡さない。
- `.agents/skills/run-phase-loop/references/reviewer-contract.md`の形式に従う。
- `REVIEW_NONCE`、reviewed tree、bundle hash、test log hash、実thread IDを照合する。
- PASSログがあっても、差分から保証できない主張は未証明として扱う。
- blockerはfrozen acceptanceに対する具体的P1/P2だけとし、任意のpolishを混ぜない。

## M2-C adversarial checklist

- exact-type/base-class validationをsubclass overrideや欠落fieldで迂回できないか。
- materializer返却値がwork itemのRequest、Pair、Claim時刻、該当するmaterialization時刻へ
  結び付いているか。不一致時にAdoption/B4へ進まないか。
- authorization、approval、research evidence、policy、Swap、config、evaluation、Candidateの
  全lineageがexact ID/contentで再構成・再検証されるか。
- persisted corruptionを通常のSKIP、missing、reuse、repairへ変換していないか。
- B4のSwap/config/evaluation/Candidateが一つの短いtransactionでatomicか。
- restart、migration upgrade/failure/retry、concurrent writer、exact replayで収束するか。
- `evaluated_at`をfrozen authority instantとして使い、後日のexpiry/revocationで過去replayを
  壊していないか。
- real two-Pair integrationが実M2-B5 materializer、Live Adoption、B4 SQLite storeを通るか。
- `live_candidates`、Portfolio、Risk、Execution、Paper、Brokerへの新規write/callがないか。
- 2 Pairの結果を順序どおり保持し、失敗時に自動retryや次Pair継続をしていないか。
- migration番号、architecture、README、ExecPlan、test strategyが実差分と一致するか。

## Approval threshold

全required checksが同じreviewed treeで成功し、上記境界にactionable P1/P2がなく、
frozen acceptanceを差分とテストから立証できる場合だけ`VERDICT: APPROVE`とする。
