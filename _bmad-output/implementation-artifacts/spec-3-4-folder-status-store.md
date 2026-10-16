---
title: 'Story 3.4: Folder Status store with lease and attempt counting'
type: 'feature'
created: '2026-10-15'
status: 'in-progress'
route: 'dispatch'
review_loop_iteration: 0
baseline_commit: '1fab27e'
context: ['{project-root}/_bmad-output/planning-artifacts/architecture/architecture-calls-log-2026-10-14/ARCHITECTURE-SPINE.md']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** A Folder must never be processed twice at once, never be lost half-way, and never be retried forever. There is no place that records where a Folder is.

**Approach:**
- `cloud/status.py` owns the lifecycle in DynamoDB: AD-4 transitions as conditional writes, an AD-5 lease per Person-Day, and AD-6 attempt counting.
- The `calls-log-folders` table and the deploy role's rights for it come in a second PR stacked on Story 2.4 (#9), because the bootstrap must build on 2.4's.

**Decisions** (the Owner said to work uninterrupted; recorded here):
- **Keys:** `pk = pd#<Person-Day>`; `sk = up#<uploadId>` for an upload, or `lease`. Timestamps are UTC ISO 8601; the lease expiry is epoch seconds.
- **Transitions:**
  - `create` gives Uploading (refused if the item exists);
  - `mark_ready`: Uploading → Pending;
  - `begin_run`: takes the lease, then Pending, Failed or Processing → Processing with attempts + 1, or → Parked when attempts are already 3;
  - `finish`: Processing → Done (clears `lastError`) or Failed (`lastError`, at most 500 characters);
  - `requeue`: Done, Failed or Parked → Pending with attempts 0.
- **Processing → Processing** (not drawn in AD-4) is a redelivered message after a Run timed out without writing Failed. It's safe because it needs the lease: 1,000 s, which outlives the 900 s timeout and expires before SQS redelivers at 1,200 s.
- **Lease:** `begin_run` takes it first, and a miss raises `Busy` without counting an attempt. A refused or parked start gives the lease back. `release_lease` acts only for its holder.
- **No AWS import:** the low-level client is passed in, with plain expression strings, so the PC and tests need no boto3.

## Boundaries & Constraints

**Always:** every write conditional on the prior state; nothing else writes the table (AD-4).

**Never:** S3 moves or deletes to signal state; a retry that isn't counted.

</frozen-after-approval>

## Tasks & Acceptance

**Execution:**
- [x] `cloud/status.py`.
- [x] `tests.py` -- a fake DynamoDB that evaluates the expressions and, like DynamoDB, refuses undefined or unused placeholders. 35 tests: all 30 operation × state pairs (allowed ones succeed; others raise and leave the item unchanged), a duplicate create, the lease against a second Run (no attempt counted) and after expiry, attempts counted first and Parked at the 4th start, the lease released only by its holder, timestamps and `lastError`.
- [ ] `infra/status.tf` and bootstrap rights -- in the stacked PR on #9.

**Acceptance Criteria:** as in epics.md Story 3.4. AD-1's "no core module imports cloud" test came with Story 3.3.

## Implementation Notes

- 2026-10-15, inline. `run_tests.py`: 213 passed (35 new).
