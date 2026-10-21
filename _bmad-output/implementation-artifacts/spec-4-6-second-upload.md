---
title: 'Story 4.6: a second upload of a day waits for a Re-run; a 4-hour runaway window'
type: 'feature'
created: '2026-10-21'
status: 'built'
route: 'dispatch'
baseline_commit: 'main (e0ca764)'
context: ['{project-root}/_bmad-output/implementation-artifacts/spec-4-5-owner-tab.md', '{project-root}/_bmad-output/implementation-artifacts/spec-3-8-reprocess-and-resend.md']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**The Owner's questions (2026-10-21):** "will the system run for 2 hrs each batch or until all folders are processed? and whats the rerun option when there are 2 uploads".

**Answers that set the scope:**
- **A batch runs until the queue is empty, two at a time.** 2 hours is only FR-5's turnaround target. But `calls-log-runner-runaway` stopped the runner after 1 h 45 min of both slots busy (FR-18's $0.50 cap), which a big week can trip.
- **Two uploads of a day were never combined.** The newest one ran by itself and replaced the day.

**The Owner's decisions:**
- **Runaway window:** "Raise it to about 4 hours". The cap becomes $1.05 past the free tier, which is 16 quarter-hours at 2,048 MB, worst case $1.02. This replaces FR-18's $0.50.
- **A second upload:**
  - It gets a Re-run option and does not run by itself.
  - If the earlier upload hasn't reached the Sheet yet:
    - Re-run → the latest is pushed;
    - otherwise the earlier one is.
  - If the earlier one was already pushed:
    - no Re-run → it stays;
    - Re-run → the latest overwrites it.
- **Who may Re-run a waiting second upload:** "Anyone with the upload passphrase". Before this change, a second upload replaced the day by itself, so this grants nothing new.

## Boundaries & Constraints

**Always:**
- `cloud/status.py` stays the only writer of Folder Status.
- Two Runs of one day never overlap (the lease).
- Whichever upload was chosen last is the last Day Sheet written and sent.

**Never:**
- combining two uploads' images;
- the upload passphrase re-running anything but a Held upload.

</frozen-after-approval>

## Design

**New states:**
- **Held:** at Ready, another upload of the day had already been made Ready, or was imported from the PC. Nothing is queued, and there is no `readyAt`.
- **Superseded:** an upload whose Run started after a later upload was chosen. It writes no Day Sheet and sends no sync.

**The choice:** the upload of the day made Pending last (`readyAt`, then `uploadId`).
- `FolderStatus.requeue` (any Re-run) starts from Done, Failed, Parked, Held or Superseded and sets `readyAt`.
- `begin_run`, after taking the lease, writes Superseded when `chosen_later` finds a later choice.
- Under the lease, the choice therefore writes last in every order:
  - an earlier upload that starts later steps aside;
  - a later upload that waited for an earlier one's lease runs after it.

**Routes and tools:**
- `/api/ready` answers `Held` and queues nothing.
- `/api/rerun` (upload passphrase): Held only, through `cloud/requeue.py` (images still in `inbox/`, Pending, one message).
- `/api/owner/requeue` and `enqueue.py --requeue` accept every re-runnable state.
- `enqueue.py` holds a second folder and prints its `--requeue … --upload-id …` line.

**Page:**
- The Status tab shows each day's current upload, then each Held upload on its own row: "Waiting for Re-run", a hint, and **Re-run (use this upload)**.
- Ready says how many folders wait.
- EN and VI.

**Infra:**
- The runner gets `dynamodb:Query` on the table (no index, no Scan). The boundary already allows it, so no bootstrap is needed.
- `runaway_cap_usd = 1.05`.

## Verification

- `python -X utf8 run_tests.py`:
  - Held at Ready;
  - an unfinished upload doesn't hold, an import does;
  - every Re-run ordering;
  - the runner's Superseded outcome;
  - the routes;
  - `enqueue.py`;
  - EN/VI words.
- `terraform test` in `infra/`: the runaway numbers, and the runner's Query on the table only.
- **Live (the Owner):**
  - upload a day twice: the second says "Waiting for Re-run" and the Sheet keeps the first;
  - Re-run it: it runs and overwrites the day's tab;
  - the runaway alarm says 16 quarter-hours.
