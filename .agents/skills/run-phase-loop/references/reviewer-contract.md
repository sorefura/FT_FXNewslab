# Reviewer contract

Each review attempt must use a newly spawned thread with the manifest's reviewer agent. Never send a
follow-up to an earlier reviewer. Record the real thread ID; the gate rejects reuse across unit and
final reviews.

Pass only the generated bundle path. The bundle contains:

- immutable copies of every frozen design and acceptance file;
- `changes.diff` from the unit or phase baseline through the current Git-reviewable tree;
- `tests.json` and command logs from the latest successful check run;
- `review-request.md` with unit and identity metadata.

The first non-empty verdict line must be exactly one of:

```text
VERDICT: APPROVE
VERDICT: REQUEST_CHANGES
```

The second non-empty line must echo `REVIEW_NONCE: <nonce>` from `review-request.md`. This binds the
returned verdict to one prepared bundle and attempt.

An approval must contain no blocking findings. A request for changes must contain at least one
substantive finding after the header. The gate stores the verdict hash and thread ID but does not
interpret engineering correctness; independence and model judgment remain the reviewer boundary.

The project reviewer is read-only but can still read the repository. The native gate validates the
bundle nonce and uniqueness of the coordinator-supplied thread ID; it cannot independently query the
Codex thread registry. When strict identity and input isolation are required, start a separate App
Server thread whose readable workspace is the bundle directory and record its returned thread ID.
