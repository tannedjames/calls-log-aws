---
title: 'Epic 5 (Stories 5.1-5.4): the tools and runbook for a safe switch-over'
type: 'feature'
created: '2026-10-16'
status: 'built; the Comparison Period, the rehearsal and cutover are the Owner''s, on the live system'
route: 'dispatch'
review_loop_iteration: 1
baseline_commit: 'claude/epic-4-upload-page'
context: ['{project-root}/_bmad-output/planning-artifacts/architecture/architecture-calls-log-2026-10-14/ARCHITECTURE-SPINE.md']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Epic 5 is mostly the Owner's work on the live system: two weeks of side-by-side running, a rehearsed way back, a rotated secret. It needs tools that make each step one command and prove the result, and a runbook that keeps the order.

**Approach:** PC tools over the AWS CLI (`tools/aws_cli.py`, no boto3), each read-only unless it says otherwise, plus the runbook in `docs/TECHNICAL.md` §11, a one-page Operator guide and a log template.

**Decisions** (D-28, the Owner's standing instruction: work uninterrupted; recorded here):
- **5.1 `tools/import_outputs.py`:** per Person-Day, every `(n)` copy is uploaded as a version of `results/<Person-Day>.xlsx` (`--checksum-algorithm SHA256`, metadata `import-sha256`), the file `resync_output.collect_files` picks uploaded last, so it is current. `cloud/status.py` gains `record_import`: Done, `imported`, no images and **no `upDay`**, so years of history never fill the page's 7-day view. A day already imported is skipped (a DynamoDB scan for `imported`, once). A day AWS processed itself is left alone, so its own Day Sheet stays current. A day cut short is recognised by its metadata and finished. `--verify`: every PC day present, then 10 random imported days' S3 SHA-256 against the PC file.
- **5.2 `tools/compare_outputs.py`:** per day in range, the PC's chosen file against the current AWS one, both read with `resync_output.read_sessions`; IDENTICAL, the first differing Session, or MISSING on either side; output in F-labels, dates, counts and times only, the label key written only outside the repo. Exit 0 only when all are IDENTICAL.
- **5.3 `tools/switches.py`:** both switches in one table with the one change for each; it reads, never writes. The switch-back rehearsal is a runbook procedure with its time recorded.
- **5.4:** `tools/check_secret.py` proves which secret the live Web App accepts by syncing the made-up name `zz-secret-check`: the Web App checks the secret before the name, so neither answer reaches a sheet. `tools/turnaround.py` gives Ready-to-Done median and p95 from the uploads-by-day index; `status.py` now records `readyAt` at Ready and at re-queue.
- **Settings cache 15 minutes (amends AD-9's "once per container"):** a rotated secret or a new employee list would otherwise reach warm runner containers only when Lambda recycles them, and syncs in between would fail with "bad secret" and park Folders. One GetParameters per container per 15 minutes is free.

## Boundaries & Constraints

**Always:** `cloud/status.py` stays the only table writer; tool output that may be shared carries no name; the PC is never changed by these tools.

**Never:** an import that overwrites a Day Sheet AWS made; a key file inside the repo; a probe that could write to a sheet.

</frozen-after-approval>

## Code Map

- `tools/import_outputs.py`, `compare_outputs.py`, `switches.py`, `turnaround.py`, `check_secret.py`.
- `cloud/status.py` (`record_import`, `readyAt`), `cloud/settings.py` (`CACHE_SECONDS`).
- `docs/TECHNICAL.md` §1, §11; `docs/OPERATOR_GUIDE.md`; `docs/aws/comparison-log.md`.

## Tasks & Acceptance

- [x] Tools and status changes, 17 tests; 9 mutations each caught.
- [x] Runbook, Operator guide, log template.
- [ ] **Owner:** import with `--dry-run`, then for real, then `--verify` (needs the deployed table and bucket).
- [ ] **Owner:** two weeks of the Comparison Period, logged; 100% IDENTICAL.
- [ ] **Owner:** the timed switch-back rehearsal (target ≤ 30 min).
- [ ] **Owner:** cutover day: the secret rotated and checked, writers switched, the Operator on the page; the first week logged.

**Acceptance Criteria:** epics.md Stories 5.1-5.4. Offline, `tests.py` pins the version order, the re-run and AWS-made guards, the interrupted import, the verify count and checksums, the F-label output and key refusal, the switch table, `readyAt`, the import item kept out of the page, the p95 and the secret probe. The rest is the Owner's on the live system.

## Verification

- `python -X utf8 run_tests.py`: 305 passed.
- `python -X utf8 tools/check_private_data.py`: OK.
