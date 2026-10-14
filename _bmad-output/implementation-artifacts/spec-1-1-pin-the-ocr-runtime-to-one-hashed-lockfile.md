---
title: 'Story 1.1: Pin the OCR runtime to one hashed lockfile'
type: 'chore'
created: '2026-10-14'
status: 'done'
route: 'oneshot'
review_loop_iteration: 0
context: ['{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `requirements.txt` holds only version ranges (`paddleocr>=3.0`, `paddlepaddle>=3.0`), so two installs can resolve different OCR packages and models and give different results. `opencv-python` is kept at 4.10.0.84 only by a constraints file outside the repo. Without that file it resolves to 5.x, and two packages then write `cv2`.

**Approach:** Add `requirements.lock`, which pins this machine's 2026-10-14 set exactly with SHA-256 hashes. One universal file (with platform markers) serves the Windows PC and the Linux image. Generate it with `uv pip compile` from `requirements.txt`, constrained by the freeze. In `requirements.txt`, pin `opencv-python==4.10.0.84` so that ranges-only installs avoid the `cv2` clash too. The docs then tell everyone to install with `pip install --require-hashes -r requirements.lock`.

**Decisions:**
- Package set: this machine's freeze (`_bmad-output/planning-artifacts/pip-freeze-this-machine-2026-10-14.txt`), chosen by the Owner (D-14).
- Python: 3.13 only, matching the lock. The docs drop "3.10+".
- `uv` is needed only to regenerate the lock; installing needs only pip. The regeneration command goes in the lock's header and in `docs/TECHNICAL.md`.
- The office PC moves to the lock by building a fresh venv, since installing over an old venv leaves extra packages such as torch in place.
- Done when: a clean Python 3.13 venv installed with `--require-hashes` from the lock has a `pip freeze` equal to the chosen freeze; `run_tests.py` passes in that venv; a `diagnose.py` OCR smoke test on a Synthetic Set screenshot works in it; regenerating the lock without the freeze constraint gives the same pins; README and TECHNICAL say to install from the lock, and `requirements.txt` says it holds ranges only.

</frozen-after-approval>

## Implementation Notes

- `requirements.lock` (new, 196 KB): 76 entries, 2,262 SHA-256 hashes, generated with uv 0.13.1 in universal mode for Python 3.13. Pass 1 was constrained by the freeze. Pass 2, the documented command with no constraint, produced identical pins and hashes, so the lock no longer refers to the planning-artifact freeze. Platform markers: `colorama` and `tzdata` are Windows-only, and `httpx2-jsfetch` is browser-only (emscripten) and never installs.
- `requirements.txt`: header says ranges only and points to the lock; `opencv-python>=4.5` became `==4.10.0.84`, with the reason. This replaces the constraints file kept outside the repo (D-3).
- Docs: `README.md` setup (Python 3.13, install from the lock, fresh venv when migrating); `docs/TECHNICAL.md` §2 (why the lock, migrating, how to regenerate or raise one package) and §8 (long-path install failure).
- Verification (2026-10-14):
  - Clean Python 3.13.15 venv, `pip install --require-hashes -r requirements.lock`: exit 0. Its `pip freeze` equals the chosen freeze, 75 of 75 packages.
  - `run_tests.py` in that venv: 85 passed, 0 failed.
  - `diagnose.py` on Synthetic Set `shot_01.png`: 7 usable call rows. The output matches the project `.venv` line for line (66 of 66).
  - Linux dry run (`uv pip install --dry-run --python-platform x86_64-manylinux_2_28`): all 73 Linux packages resolve with matching hashes, including paddlepaddle 3.3.1 and both OpenCV packages at 4.10.0.84.
- Surprise: the first clean install failed on Windows' 260-character path limit, because the scratchpad path is about 130 characters before the venv's own paths. Retried in `%TEMP%\cl-lockvenv`, which was removed afterwards. Documented in TECHNICAL §8.
- No code changed, so no `graphify update`. `check_private_data.py` can't run without git; the changed files hold no employee data.

## Review Triage Log

- low (patched): the long-path install failure wasn't documented, and the office PC could hit it if its folder sits deep. Added to TECHNICAL §8.
- low (rejected): the lock lists hashes for every platform's wheels (196 KB). pip checks only the file it downloads, and trimming the hashes would mean two lockfiles, against the one-lock rule.
- low (rejected): `AGENTS.md` doesn't mention the lock. Install steps live in README and TECHNICAL, which both point to it; agent rules aren't edited by a story.
- false: "the lock might pin a different paddlex than production." The Owner chose this machine's set as the baseline (D-14). The office PC moves to it by reinstalling.
