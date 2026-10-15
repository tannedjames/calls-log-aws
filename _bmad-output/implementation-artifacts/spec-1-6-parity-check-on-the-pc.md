---
title: 'Story 1.6: Parity check on the Owner''s PC'
type: 'feature'
created: '2026-10-15'
status: 'in-progress'
route: 'oneshot'
review_loop_iteration: 0
context: ['{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Nothing yet proves that the runner image reads the Owner's real screenshots exactly like the PC. The Reference Set holds customer phone numbers, so the proof has to run on the PC and nothing may leave it.

**Approach:** `tools/parity_check.py --image <tag> --ref <dir>` runs the image on each reference folder and compares the Sessions with a private expectations file, both outside the repo. Each folder is mounted read-only at `/ref/F<n>`, with `--network none --read-only --tmpfs /tmp` and a non-root user. The tool passes the image a short program that runs `main.extract_calls` → `dedupe` → `build_sessions`, the batch path minus writing, syncing and archiving, so any image tag can be tested. It prints IDENTICAL or DIFFERENT per folder, with the first differing Session.

**Decisions:**
- The expectations file defaults to `<ref>/parity-expected.json` and lists each folder's name, the files to ignore (`correct output.png`, the reference screenshot) and the expected Sessions. Labels F1…Fn follow the file's order. The output shows only labels, counts and times, never names, paths or phone numbers.
- This PC's file is `C:\Users\admin\Downloads\calls-log\parity-expected.json`:
  - F1 and F2 = the unedited PC output;
  - F3 = the Owner's rule-based result (D-11).
- The tool refuses a `--ref` or expectations file inside the repo.
- Proof that nothing was written: each reference folder's file list, sizes and modified times are compared before and after the run; any change fails the check. The mounts are read-only in any case.
- Exit codes: 0 = all IDENTICAL; 1 = any DIFFERENT or a reference folder changed; 2 = bad input or Docker unavailable.
- Docker CLI: `docker` from PATH, else Docker Desktop's default install path, or `--docker`.
- TECHNICAL documents the setup (Docker Desktop on WSL 2, the expectations format, the command), and that the check is re-run after any change to the lock, a model or the image.
- Done when: all three folders are IDENTICAL with `calls-log-runner:local` built from this repo, the reference folders are unchanged, unit tests pass without Docker, and the privacy check passes.

</frozen-after-approval>

## Implementation Notes
