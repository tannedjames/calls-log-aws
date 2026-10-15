---
title: 'Story 1.6: Parity check on the Owner''s PC'
type: 'feature'
created: '2026-10-15'
status: 'done'
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

- New `tools/parity_check.py`. The in-container program (`INSIDE`) is sent with `python3 -c`, so any image tag can be checked, including the exact one later pushed to ECR. It uses `main.IMAGE_SUFFIXES` + sorted order, `extract_calls`, `dedupe` and `build_sessions`, as batch mode does. Mounts use `--mount type=bind,...,readonly` (paths with spaces work; a `,` in a path is refused).
- The container only ever sees `/ref/F<n>`, so even its error lines can't contain a folder (employee) name; on failure the tool prints only the last stderr line, cut to 200 characters.
- Expectations file created outside the repo at `C:\Users\admin\Downloads\calls-log\parity-expected.json`. Sessions come from the 2026-10-14 run that matched the references (D-10, D-12). F3 was checked equal to the D-11 list. Each folder was checked to hold its screenshots plus exactly `correct output.png`.
- `.gitignore`: `parity-expected*.json` as a safety net. TECHNICAL: "Parity check" section (setup, format, command). README: helper-tools row.
- Tests: 7 Docker-free tests (identical, first difference ×3, write detection, inside-repo refusal, missing expectations), 122 in all.
- Verification (2026-10-15):
  - Local build `calls-log-runner:local` with Docker Desktop 29.8.2: 267 s; native-library check and 11 verified models, as in CI.
  - Parity run: **F1 3 Sessions / 101 countable calls, F2 5 / 96, F3 6 / 104, all IDENTICAL**; exit 0; 322 s; reference folders unchanged.
  - Negative run with a deliberately wrong F1 expectation (temporary file, deleted after): `DIFFERENT (session 2: expected 11:04-12:05, got 11:04-12:01)`, exit 1.
  - `graphify update .` run.
- The first commit of this story (`3b1c97d`) went in before these notes, because the script meant to add them failed on a Windows path escape. They were added in a follow-up commit.

## Review Triage Log

Inline review, at the Owner's choice.

- low (patched): nothing in the repo would stop a stray copy of `parity-expected.json` (real names and hours) from being committed; the privacy check can't see names on a PC without `Employees.gs`. `.gitignore` now ignores `parity-expected*.json` everywhere.
- false: "bind-mount reads could change the fingerprint." The fingerprint uses size and modification time, not access time; the real run reported no change.
- false: "a non-root container can't read Windows bind mounts." Docker Desktop's WSL 2 mounts are readable by any user; the run read all 46 images as 1000:1000.
- low (rejected): each folder starts a fresh container and pays OCR start-up (~5 s). That's negligible next to OCR time, and it keeps the folders isolated from each other.
