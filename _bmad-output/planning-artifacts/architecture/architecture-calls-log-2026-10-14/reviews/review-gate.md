# Reviewer gate: ARCHITECTURE-SPINE.md (2026-10-14)

**Method note.** These three reviews were run **inline by the authoring agent, in sequence**, not
as independent subagents. The session's rules only allow subagents when the Owner asks. The
skill's reviewer-gate warns that an inline self-check is weaker, because a fresh context finds
divergences the author talks past. Treat this gate as a floor and re-run it independently if
wanted.

Deterministic pass: `lint_spine.py` returned 0 findings.

## 1. Rubric walker (good-spine checklist)

**Verdict: adequate → strong after fixes.** The real divergence points for the epics are fixed:
state owner, keys, message shape, config, commit order, logging. Every PRD capability maps to a
unit and an AD. The operational envelope is covered (prod only, Terraform via CI, alarms, kill
switch, PC as alternative).

- **high** Intake and runner each validate folder names, so two implementations could disagree
  (AD-8). *Fix:* one shared validation function used by intake, runner, resync and import.
- **medium** The order of the AD-5 lease and the AD-6 attempt count is unstated, so a lease miss
  could burn an attempt. *Fix:* take the lease first; a lease miss isn't an attempt.
- **medium** AD-10 is silent on resync. `resync_output.py` forces sync on
  (`resync_output.py:131`). *Fix:* resync mode is an explicit Owner action and ignores the AWS
  Sync Switch, mirroring the PC.
- **medium** AD-1 has no enforcement. *Fix:* a test asserts that no core module imports `cloud`.
- **medium** The S3 and DynamoDB protection baseline (risk ADV-10) isn't in the spine; it lives
  only in the PRD. *Fix:* new AD-18.
- **low** A ULID `uploadId` adds an unpinned dependency. *Fix:* UTC timestamp plus random hex,
  from the standard library.

## 2. Web-verification reviewer (finalize_reviewers[0])

| Claim | Status |
|---|---|
| Lambda `python3.13` on AL2023, base image tag `3.13` | Verified (AWS runtime and image docs) |
| Terraform ≥ 1.10 `use_lockfile`; `hashicorp/aws ~> 6.56` (6.57.0 pulled) | Verified (third-party release trackers; check the Registry at build) |
| `configure-aws-credentials@v6` | Verified via a third-party index; GitHub docs show v6-era examples |
| paddlepaddle 3.3.1 cp313 x86_64 only | Verified (PyPI) |
| SQS ESM `MaximumConcurrency` 2–1000, visibility timeout ≥ function timeout | Verified (AWS docs); FIFO support unconfirmed, so the design uses a standard queue |
| **Reserved concurrency needs the account limit to leave 100 unreserved; new accounts may have a limit of 10** | **Verified: blocker for AD-11 as written.** *Fix:* the kill switch disables the event source mapping (no reservation needed); bootstrap requests a concurrency quota increase; `MaximumConcurrency` 2 alone caps the runner if reservations aren't possible |
| **Function URL auth NONE needs both `lambda:InvokeFunctionUrl` and `lambda:InvokeFunction` (from Oct 2025)** | **Verified: missing from the spine.** *Fix:* new AD-17 |
| `mesa-libGL` available via `dnf` on the AL2023 Lambda image | **Unverified.** Phase 0 build smoke test; the alternative `--no-deps` swap to `opencv-contrib-python-headless==4.10.0.84` is listed in Deferred |
| Fargate Singapore prices | Unverified, tagged [ASSUMPTION] in the solution design |

## 3. Adversarial pair constructor (finalize_reviewers[1])

| Pair | How two compliant units still diverge | Fix |
|---|---|---|
| Intake vs runner | Each normalises and validates names its own way (AD-8 named only `parse_name_date`) | Shared `cloud/naming.py` (AD-8) |
| Import tool vs PC `resync_output.py` | Import makes the highest `(n)` copy current; the PC's resync prefers the base file, so AWS resync sends different data | AD-8: import makes current the file `resync_output.collect_files` would pick |
| Terraform env vs `settings.py` | Terraform names a variable differently from what `settings.py` reads | AD-9: `settings.py` is the only reader; Terraform builds env vars from one `locals` map whose keys match `settings.py` |
| Kill switch vs reserved concurrency | The kill switch sets concurrency to 0 on an account that can't reserve, so the call fails and the runaway continues | AD-11: disable the ESM |
| Status page vs runner timestamps | One writes local time, the other UTC | Conventions already require UTC; the page converts for display |
