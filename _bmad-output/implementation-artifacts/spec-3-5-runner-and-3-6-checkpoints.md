---
title: 'Stories 3.5 and 3.6: the runner processes one Folder end to end, with per-image checkpoints'
type: 'feature'
created: '2026-10-15'
status: 'done'
route: 'dispatch'
review_loop_iteration: 0
baseline_commit: 'de6466f'
context: ['{project-root}/_bmad-output/planning-artifacts/architecture/architecture-calls-log-2026-10-14/ARCHITECTURE-SPINE.md']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Nothing takes a Ready Folder through the pipeline on AWS. A 40-image Folder can also hit Lambda's 15-minute limit and fail forever.

**Approach:** `cloud/runner.py` runs one Folder with the unchanged core in main.py's order (AD-2, AD-7), checkpointing each image (AD-16). `cloud/storage.py` is the S3 adapter. It has three entry points sharing one code path: the Lambda `handler`, `python -m cloud.runner --upload-id … --person-day …`, and a no-AWS `--local` mode for parity (AD-15). Story 3.6 ships here because the checkpoints sit inside the same loop.

**Decisions** (the Owner said to work uninterrupted; recorded here):
- **Order:** files sort the way the Owner's Windows PC sorts `Path`s, case-insensitively (`storage.pc_order`), so dedupe's "keep first" matches the PC. Linux's case-sensitive `sorted()` would differ for mixed-case names.
- **Per image:** `main.extract_calls([one image])`, records concatenated in order, which gives the same records as one call over all files (`tools/check_runner_parity.py` checks it with real OCR). Each image's records go to `work/<uploadId>/<file>.json`. A resumed Run loads them and OCRs only the missing images (3.6).
- **Commit order (AD-7):**
  1. `begin_run` (lease, then attempt), then the images;
  2. `dedupe` → `build_sessions`; zero Sessions → Failed with main's message, no Day Sheet;
  3. `write_sessions(sprs.xlsx)` → `results/<Person-Day>.xlsx`;
  4. the Sync Switch read fresh → `configure_core` → `sync_day` if on;
  5. Done.
  Any exception → Failed (`lastError`, with the secret and URL masked) and `RunFailed`, so the message returns.
- **Outcomes:** Busy (lease held: the message returns, no attempt); Parked (the 4th start: an SNS email naming the Folder and the last error, then the message is deleted); Skipped (not startable, e.g. a duplicate delivery after Done: deleted, nothing OCR'd).
- **`handler`** reports Busy and failed messages in `batchItemFailures`; the event source mapping in 3.7 enables `ReportBatchItemFailures`. AWS clients and settings are made once per container.
- **CLI:** `--upload-id` needs `--person-day` too, because the table is keyed by Person-Day; the queue message carries both. `--local DIR` prints `PARITY_RESULT`, the line `tools/parity_check.py` reads, and its new `--via-runner` sends the Reference Set through this entrypoint.
- **Logs:** one JSON line per transition with `uploadId`, `personDay`, `status`, `attempt`, `durationMs`, `images` and `error` (plus `sessions`, `synced`, `dayStored`). Never OCR text, images or secrets. `/tmp/calls-log/<uploadId>` is removed after every Run.

## Boundaries & Constraints

**Always:** the core unchanged and called as main.py calls it; `import runtime_env` first.

**Never:** a Day Sheet for zero Sessions; Done before the Sync succeeds while the switch is on; OCR text in logs.

</frozen-after-approval>

## Tasks & Acceptance

**Execution:**
- [x] `cloud/storage.py`, `cloud/runner.py`.
- [x] `tools/check_runner_parity.py` and a CI step: the Synthetic Set read main's way and the runner's way, IDENTICAL, plus a CLI `--local` smoke run.
- [x] `tools/parity_check.py --via-runner`.
- [x] `tests.py`, 12 tests with S3, DynamoDB, SSM and SNS faked and OCR stubbed per image (the real dedupe, session rules and xlsx writer run):
  - PC order;
  - Done with the Day Sheet stored and no sync while the switch is off;
  - sync only when on;
  - a sync failure keeps the Day Sheet and fails;
  - zero Sessions fails with no Day Sheet;
  - Busy without an attempt;
  - Parked at the 4th start with the alert;
  - a duplicate message is Skipped;
  - **a Run cut off at image 3 resumes, OCRs only image 3, and writes the same Sessions as an uninterrupted Run (3.6)**;
  - JSON logs with no OCR text or secret;
  - the handler's `batchItemFailures`;
  - `--local` output.

**Acceptance Criteria:** as in epics.md Stories 3.5 and 3.6. The Reference Set row runs on the Owner's PC with `parity_check.py --via-runner`, with the Owner's OK.

## Implementation Notes

- 2026-10-15, inline, stacked on #12. `run_tests.py`: 225 passed (12 new), on the PC and inside the image.
- **Real OCR, local image build:** `tools/check_runner_parity.py --dir /synth` (offline, read-only, non-root, 2 CPUs) gives IDENTICAL for all three synthetic sets (4, 3 and 3 Sessions). `python3 -m cloud.runner --local …` prints the `PARITY_RESULT` line.
- A script in `tools/` needs the repo root on `sys.path` before `import runtime_env`, as `check_synth.py` does.
- The `work/` 30-day lifecycle rule already exists (Story 2.4, `infra/data.tf`).
- Lambda, SQS and the IAM role come in Story 3.7. Until then `handler` is exercised by unit tests only.
