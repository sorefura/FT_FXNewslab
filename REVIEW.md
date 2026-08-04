# Independent Review Standard

Codex、実装agent、HANDOFFの「完了」「テスト成功」という申告は証拠として扱わない。
reviewerはfrozen design、生成されたimmutable bundle、差分、実ログだけから独立に判定する。

## Identity and evidence rules

- attemptごとに新しいreviewer threadを作り、過去のreviewerへfollow-upしない。
- reviewerはread-onlyとし、live repositoryや過去会話を渡さない。
- `.agents/skills/run-phase-loop/references/reviewer-contract.md`の形式に従う。
- `REVIEW_NONCE`、reviewed tree、bundle hash、test log hash、実thread IDを照合する。
- PASSログがあっても、差分から保証できない主張は未証明として扱う。
- blockerはfrozen acceptanceに対する具体的P1/P2だけとし、任意のpolishを混ぜない。

## M2-D adversarial checklist

- accepted M2-A/M2-C identityを変更またはsilent reinterpretしていないか。
- opaque checkpoint/decision IDだけでAdoption、Signal、Swap terminal outcomeを
  「証明」していないか。additive typed resolutionとexact parent contentを検証するか。
- Position capacityがPosition IDだけでなくevidence ID、Pair、Side、observed time、
  source/checkpoint、BASE_UNITS、positive finite Decimal quantityへ結び付くか。
- Strategyがquantityを決めていないか。Portfolio以外がquantityを拡大・置換していないか。
- threshold/freshness/effective-window/holding-age equalityとtrigger precedenceが
  frozen specどおりか。future evidenceを通常KEEPへ変換していないか。
- exact-type/base-class validationをsubclass override、欠落field、比較override文字列で
  迂回できないか。outcome routingやwriteより前に拒否するか。
- identical replayが後発reservationを再計算せず元のsemantic chainを返すか。
- distinct concurrent writerが同一Positionをcapacity IDの差替えでover-reserveできないか。
  B4のreservation読取、
  Portfolio、Risk、Intent appendが一つの`BEGIN IMMEDIATE`境界か。
- reservation snapshotが実際に使った全prior Intent ID/quantityを順序付きでcommitするか。
- corruption/missing parentをREJECT、reuse、repair、retryへ変換していないか。
- KEEP/CLOSE、Portfolio/Risk、Intentのcardinalityが全transaction failureで保たれるか。
- DecimalをREAL/floatへ落とさず、lossless textでno-overcloseを証明するか。
- ordinary close型/tableがentry decisionや`ApprovedLiquidationIntent`へunion、inheritance、
  action string、legacy writeで接続されていないか。
- `LIVE`がStrategy/storeより前に拒否され、Execution/Paper/Broker/Private callがないか。
- migration `0005`、fresh/upgrade/reopen/failure/retry/concurrency、architecture、README、
  ExecPlan、test strategyが実差分と一致するか。

## Approval threshold

全required checksが同じreviewed treeで成功し、上記境界にactionable P1/P2がなく、
frozen acceptanceを差分とテストから立証できる場合だけ`VERDICT: APPROVE`とする。
