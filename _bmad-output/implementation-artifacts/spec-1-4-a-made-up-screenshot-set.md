---
title: 'Story 1.4: A made-up screenshot set with known answers'
type: 'feature'
created: '2026-10-14'
status: 'done'
route: 'oneshot'
review_loop_iteration: 0
context: ['{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** OCR can be checked end to end only on real screenshots, which hold customer phone numbers and must never leave the PC. CI and the Linux image therefore have nothing to regression-test OCR against.

**Approach:** Move the benchmark prototype into the repo as `tools/gen_synth.py`. It renders fictional call-log screenshots, photo variants and sideways photo variants from a seed, with a ground-truth `truth.json`. Add `tools/check_synth.py`, which reads each set through the real pipeline (`main.extract_calls` + `dedupe`, so nothing is synced, written or moved) and compares the parsed calls with the truth. It exits non-zero on any difference.

**Decisions:**
- Content is fictional only: folder `Nguyen Van A & 2026-06-01`, invented contact labels, no phone numbers.
- Three sets, defaults 12 / 4 / 2 images:
  - **screenshots** (PNG);
  - **photos**: screenshots composited onto a dark desk, slightly tilted and blurred;
  - **sideways**: those photos turned 90° and 270°, which guards the landscape classification kept by Story 1.2.
- `truth.json` lists the expected calls per set explicitly, oldest first.
- Determinism: the same seed gives the same `truth.json` everywhere and byte-identical images with the same font. The font is auto-detected (Yu Gothic or Meiryo on Windows, Noto Sans CJK on Linux) or passed with `--font`; with none found, the generator stops with a clear message.
- Output goes by default to `synthetic/`, which is gitignored. Images are already ignored everywhere, so nothing generated can be published.
- `check_synth.py --threads N` caps the OCR threads for CI runners.
- A model-free unit test pins the plan: same seed, same truth; a different seed, a different day; each subset's truth is exactly the calls its images show.
- Docs: TECHNICAL §2 (how to run both tools) and README's helper-tools table.
- Done when:
  - generating twice with one seed gives identical files;
  - `check_synth.py` matches every call in all three sets on this machine;
  - `run_tests.py` passes;
  - the privacy check prints `OK`.

</frozen-after-approval>

## Implementation Notes

- New `tools/gen_synth.py`, built from the benchmark prototype. All randomness is drawn in `plan()` from the seed, so the truth is testable without a font; rendering is separate. Labels that looked like real surnames (田中, 山田) were replaced with placeholder ones (見本 一郎 様, 試験 次郎 様).
- New `tools/check_synth.py`. It uses `main.extract_calls` and `gather_jpg_files` plus `session_builder.dedupe`, the same path as `main.py` minus writing, syncing and archiving. `--threads` sets `OMP_NUM_THREADS`/`MKL_NUM_THREADS` before `runtime_env` loads, and sets `CONFIG.paddle_cpu_threads`.
- Safety: `write_set` deletes only the three fictional folders, and only when `truth.json` shows this tool wrote them. Pointing `--out` at a folder that holds anything else stops with an error (tested).
- `.gitignore`: `synthetic/`. Images were already ignored everywhere.
- `tests.py`: 3 model-free plan tests (determinism, subset truth, no identical calls across 200 seeds). Docs: TECHNICAL §2 and the README helper-tools table.
- Verification (2026-10-14):
  - Same seed generated twice: all 19 files byte-identical (SHA-256); seed 7 gives a different `truth.json`.
  - `check_synth.py --threads 4` on the default set: screenshots 121/121, photos 41/41, sideways 21/21, exit 0, 203 s.
  - Second seed after the duplicate fix (seed 7, 4/2/2 images): 41/41, 21/21, 21/21, exit 0. It took over 10 minutes because the machine was under heavy load at the time; that isn't a measurement.
  - `run_tests.py`: 97 passed.
  - Privacy check through a temporary git index (`GIT_DIR` in `%TEMP%`, this folder as the work tree, so nothing was created in the project): `OK`, 291 files, no `synthetic/` and no images listed. **Limit:** `secrets_local.py` and `apps_script/Employees.gs` don't exist on this machine, so the secret and name values weren't checked. Run the check on the office PC before any push.
- `graphify update .` run after the code changes.

## Review Triage Log

- medium (patched): the generator could draw two identical calls (same minute, kind and length). `dedupe()` merges those, so the checker would report a false mismatch; under the old rule that happened for 36 of 200 seeds. Such a call now moves one minute on. The default seed's output is unchanged (byte-identical), so its OCR result stands. Pinned by `test_synth_truth_never_holds_identical_calls`.
- low (patched): a set folder missing from disk raised a traceback. It's now reported as a MISMATCH with a hint.
- low (patched): sorting the mismatch list could raise on a parsed row without a start time (`None`); it now sorts with `key=str`.
- maybe-false (deferred to Story 1.5 / 2.2): rendering with Noto Sans CJK on Linux isn't verified here. The truth is font-independent, but whether OCR matches on Linux-rendered images shows up only in the image/CI run. That run is where a regression would surface, which is the purpose of the check.
- low (rejected): the checker could also compare Sessions, not only calls. Sessions are derived by the already-tested rules, and call-level differences are the more precise signal.
