---
title: 'Story 3.8: re-queue stored Folders and re-send stored Day Sheets'
type: 'feature'
created: '2026-10-16'
status: 'built, live checks wait on AWS access'
route: 'dispatch'
review_loop_iteration: 0
baseline_commit: '3714e7d'
context: ['{project-root}/_bmad-output/planning-artifacts/architecture/architecture-calls-log-2026-10-14/ARCHITECTURE-SPINE.md']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** A Parked or Failed Folder can only be fixed by uploading it again, and stored Day Sheets can't be re-sent to the Sheets without the PC (FR-7, FR-13).

**Approach:** Two PC tools on the AWS CLI, and a runner mode. `tools/enqueue.py --requeue` resets a stored upload to Pending and queues it. `tools/resync.py` lists `results/`, filters by name and date, and queues one `resync` message per day. The runner's `resync` mode re-sends the stored Day Sheet with the unchanged `sync_day`. The Owner approved this plan on 2026-10-16 ("keep working").

**Decisions:**
- **Re-queue:**
  - picks the newest upload of the Person-Day (uploadIds start with UTC time) or `--upload-id`, read with a new read-only `FolderStatus.uploads()` (Query on `pk`, `sk` beginning `up#`);
  - only from Done, Failed or Parked, through the existing `FolderStatus.requeue()` (Pending, attempts 0), so `cloud/status.py` stays the only writer (AD-4);
  - refused when fewer images than `imageCount` remain in `inbox/`, which expires after 30 days: send the folder again;
  - one `process` message, as `enqueue.py` sends.

  Earlier Day Sheets stay as S3 versions (FR-12).
- **Re-send (AD-10):**
  - the message is `{"personDay", "mode": "resync"}`. There is no uploadId, because a re-send isn't an upload; the spine's schema lists one for `process` messages;
  - the runner downloads the current `results/<Person-Day>.xlsx` (`S3Storage.get_result`) and reads it back with `resync_output.read_sessions`, the PC's reader;
  - `configure_core(..., sync_on=True)` ignores the AWS switch, as `resync_output.py` does, then the unchanged `sync_day`.
- **Failure policy:**
  - a failed re-send returns its message until the 3rd delivery (`ApproximateReceiveCount`); then one SNS email and the message is dropped;
  - a missing or empty Day Sheet: one email, never retried;
  - so a broken Web App or secret can't push re-sends into the DLQ and trip the kill switch (3 < `maxReceiveCount` 10).
- **Filters:** `--name` is a prefix and `--date` a day, as in `resync_output.py`. `--date` accepts unpadded forms (FR-13: every form processing accepts). Keys in `results/` not named as the runner writes them are skipped with a count.
- **Runner role:** `s3:GetObject` on `results/*` (inside the workload boundary; no bootstrap change).

## Boundaries & Constraints

**Always:** the core unchanged (`sync_day`, `read_sessions`); one JSON log line per re-send; no secret in a log or email.

**Never:** a re-send that obeys the AWS switch; a requeue that skips the inbox check; an automatic retry of a dropped re-send.

</frozen-after-approval>

## Code Map

- `cloud/status.py`: `uploads()`.
- `cloud/storage.py`: `get_result()`, and a shared `_missing()`.
- `cloud/runner.py`: `resync_day()`, `_alert_resync()`, `RESYNC_TRIES`, and the handler's `mode` dispatch.
- `tools/enqueue.py`: `--requeue`, `--upload-id`, `requeue()`, `Refused`.
- `tools/resync.py`: new.
- `infra/runner.tf`, `infra/tests/runner.tftest.hcl`: `GetObject` on `results/*`.
- `docs/TECHNICAL.md` §1 and §11 ("Re-queueing a Folder", "Re-sending stored Day Sheets").

## Tasks & Acceptance

**Execution:**
- [x] Code, Terraform and docs as above.
- [x] `tests.py` (12 new):
  - the uploads list, paged;
  - re-queue of Done, Failed and Parked;
  - naming an older upload;
  - 4 refusals that change nothing;
  - usage checks before any AWS call;
  - a resync with the switch off still syncs;
  - retry, then drop with one email;
  - nothing stored: email, no retry;
  - the handler passes the delivery count;
  - the tool's filters and dry run, its messages, and a bad date and no match.
- [ ] **Live** (Synthetic Set, once AWS access exists):
  - re-queue a Parked Folder to Done;
  - `resync.py --dry-run`, then a real re-send with the switch off and the tab overwritten;
  - a re-send with a wrong secret: 3 deliveries, one email, no DLQ.

**Acceptance Criteria:** epics.md Story 3.8.

## Implementation Notes

- 2026-10-16, on `claude/story-3-8-reprocess`, stacked on #16.
- `run_tests.py`: 264 passed (12 new). Terraform: infra 12 passed (the runner test gains the `GetObject` set).
- **Mutations** (each reverted after its run):
  - the re-send obeys the switch;
  - a drop on the first failure;
  - the handler ignores the delivery count;
  - no expired-image check;
  - the oldest upload chosen;
  - no date normalising;
  - `uploads()` ignores paging.

  Each fails 1 or 2 tests.
- `resync_output` is imported inside `resync_day`: importing it calls `logging.basicConfig`, which shouldn't change the runner CLI's log format.

## Verification

- `terraform -chdir=infra fmt -check`, `validate` and `test`; `python -X utf8 run_tests.py`; `tools/check_private_data.py`.
