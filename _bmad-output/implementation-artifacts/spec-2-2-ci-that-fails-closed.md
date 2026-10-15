---
title: 'Story 2.2: CI that tests, fails closed on private data, and checks OCR'
type: 'feature'
created: '2026-10-15'
status: 'done'
route: 'dispatch'
review_loop_iteration: 0
baseline_commit: '38486975945f07b886c117d8caddebf6cf6b407c'
context: ['{project-root}/_bmad-output/implementation-artifacts/epic-2-context.md']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** In CI the privacy gate can't see `secrets_local.py` or `Employees.gs`, so it prints a note and passes: it can never catch a real name, ID or secret on GitHub. The unit tests don't run in CI at all, and nothing stops a red change from reaching `main`, which Story 2.3 will deploy from.

**Approach:**
- **Privacy gate:** `tools/check_private_data.py` gains `--fail-closed`, used in CI. Without the private files it insists on a **hashed denylist** from the GitHub secret `PRIVATE_DENYLIST`, and fails if there isn't one. The denylist is exported on the office PC with `--export-denylist`. It holds only keyed hashes (BLAKE2b with a random key per export) of fixed-length windows: raw text for the secret, URL and IDs, accent-folded text for names. So GitHub never receives a plaintext name, ID or secret, and a value pasted anywhere is still found.
- **One workflow:** `.github/workflows/ci.yml` replaces `runner-image.yml`, with two jobs, *Privacy gate* and *Tests, image and OCR*. The second runs `run_tests.py` inside the built runner image, plus every image check from Story 1.5. It runs on pushes to `main`, all PRs and manual runs, with no path filters, so required checks always report.
- **Pinned actions:** actions are pinned to full commit SHAs, and Dependabot checks them weekly (the item deferred from Story 1.5).
- **Protection:** a repository ruleset on `main` requires both checks.

**Decisions:**
- Local git hooks keep today's behaviour (the full check on the office PC; a file-type check plus a note where the private files are missing). Only CI is fail-closed, so work on this PC isn't blocked.
- Denylist labels never reveal a value (`employee name #3`, `spreadsheet ID #7`, `SHEETS_SYNC_SECRET`). The matching semantics equal today's check: substring match, names accent-folded.
- Pinned versions: `actions/checkout` v5 `fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09`, `actions/setup-python` v6 `ece7cb06caefa5fff74198d8649806c4678c61a1` (both proven in our CI).
- Changing the ruleset is a GitHub settings change, so it's applied with `gh api` only after the Owner approves. If the plan doesn't offer it for a private repo, Story 2.3 gates every deploy on a green CI instead, and the deviation is recorded.
- **Owner decisions (2026-10-15):**
  - Q1 (a): `main` accepts changes only through pull requests with both checks green; work happens on branches.
  - Q2: the ruleset is turned on now. Until `PRIVATE_DENYLIST` exists the privacy job is red by design, and merges wait for the Owner's office-PC step (or an explicit admin bypass).
- Done when:
  - the tests prove that a planted fake name, ID and secret are caught through a denylist, and that `--fail-closed` exits 1 with no denylist;
  - on GitHub, the privacy job **fails** while the secret is absent and the image job is green;
  - the ruleset is active (or the fallback is recorded);
  - after the Owner sets `PRIVATE_DENYLIST` from the office PC, the privacy job turns green.

</frozen-after-approval>

## Implementation Notes

- `tools/check_private_data.py` was restructured: `publishable_files`, `read_publishable` and `have_private_files` are patchable for tests. Names are now compared accent-folded on both sides (before, only the file side was folded); otherwise the local behaviour is unchanged. A denylist scan of the whole repo with 31 fictional entries takes about 6 s.
- `ci.yml` replaces `runner-image.yml` (deleted). The unit tests run inside the runner image: 128 passed locally in the Linux image (Docker Desktop) and on GitHub.
- Review (inline): Dependabot PRs can only read *Dependabot* secrets, so the docs now set `PRIVATE_DENYLIST` in both stores (`gh secret set ... --app dependabot`).
- CI run 38689546427 on `ddf461f`: **Tests, image and OCR: success** (128 tests, threads `1 1`, Synthetic Set all match). **Privacy gate: failure**, `FAILED CLOSED: ... no PRIVATE_DENYLIST was given`, exactly as intended without the secret.
- Ruleset `25140628` "main: pull requests with green CI" is active (the plan allows it on this private repo): no deletion, no force-push, PRs required (0 approvals), required checks "Privacy gate" and "Tests, image and OCR", admin bypass only inside PRs. `rules/branches/main` confirms all four.
- **Closed 2026-10-15.** There is no separate office PC: the Owner's PC (this one) is the only machine. The private files were created here (`apps_script/Employees.gs` built from the live Apps Script project, where the map sits inline in the main file; `secrets_local.py` filled in by the Owner). The full local check then covered 34 values (16 names, 15 Sheet IDs, the secret, the URL and the deployment ID) and found nothing. 34 hashed entries were exported and set as the `PRIVATE_DENYLIST` Actions and Dependabot secrets; the export file was deleted. The failed privacy jobs were re-run and went green; PRs #3, #2 and #1 were merged. Dependabot's v7 pins were verified against the official tags. CI on `main` (`94099bf`) is green on both jobs.

## Review Triage Log

- low (patched): Dependabot PRs can't read Actions secrets, so they would fail the gate. Documented setting the Dependabot secret too.
- low (accepted): the denylist file carries its own hash key, so anyone holding the GitHub secret could brute-force short names offline. GitHub keeps the secret encrypted and masks it in logs; the threat model is "never send GitHub plaintext", which holds.
- low (accepted): `strict_required_status_checks_policy` is off, so a PR needn't be rebased on the newest `main` before merging. With one person merging PRs in sequence that's rarely stale, and it saves a CI re-run each time.

## Code Map

- `tools/check_private_data.py:44` `private_values()` -- the (label, value) source for export; `:72` `norm()` -- the name folding; `:79` `main()` -- split into a scan helper, export and the fail-closed decision.
- `.githooks/*` -- unchanged (they call the tool without `--fail-closed`).
- `.github/workflows/runner-image.yml` -- folded into `ci.yml`, then deleted.
- `tests.py` -- the new denylist and fail-closed tests (stdlib only; no git or Docker needed).

## Tasks & Acceptance

**Execution:**
- [x] `tools/check_private_data.py` -- `--fail-closed`, `--denylist FILE` / env `PRIVATE_DENYLIST`, `--export-denylist FILE`; windowed keyed-hash matching.
- [x] `tests.py` -- export → plant a fake name, spreadsheet ID and secret in a temp file → caught with exit 1; clean text passes; fail-closed without a denylist → 1.
- [x] `.github/workflows/ci.yml` (+ delete `runner-image.yml`), `.github/dependabot.yml`.
- [x] `docs/TECHNICAL.md` §10 -- CI gate, exporting the denylist and setting the secret (`gh secret set PRIVATE_DENYLIST < file`, then delete the file).
- [x] Push; observe the privacy job red without the secret and the image job green.
- [x] Ruleset on `main` (after the Owner's OK) requiring "Privacy gate" and "Tests, image and OCR".

**Acceptance Criteria:**
- Given CI with no denylist secret, when the privacy job runs, then it exits 1 and says why.
- Given a PR whose CI fails, when merging is attempted, then GitHub blocks it (or the recorded fallback applies).

## Verification

**Commands:**
- `.venv\Scripts\python.exe -X utf8 run_tests.py` -- all pass, including the new tests.
- `docker run --rm calls-log-runner:local python3 -X utf8 run_tests.py` -- the same suite passes on Linux.
- `gh run view <id>` -- job conclusions as stated above.
