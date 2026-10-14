# Epic 1 Context: An OCR runtime you can pin and prove

<!-- Compiled from planning artifacts. Edit freely. Regenerate with compile-epic-context if planning docs change. -->

## Goal

Give the Owner one OCR runtime that installs identically on the Windows PC and in an x86_64 Linux container, and prove with evidence that it produces the same Sessions as the PC. The epic also lands the photo-orientation fix in the shared core, so the PC benefits before any AWS work exists. Everything later (CI gates, the Lambda runner, the cutover) relies on this epic: if the runtime can't be pinned and proven, results in AWS can't be trusted.

## Stories

- Story 1.1: Pin the OCR runtime to one hashed lockfile
- Story 1.2: Stop re-rotating upright photos
- Story 1.3: Tests that pin the behaviour the migration must preserve
- Story 1.4: A made-up screenshot set with known answers
- Story 1.5: Linux runner image (containerise)
- Story 1.6: Parity check on the Owner's PC

## Requirements & Constraints

- **No functional change.** Sessions, Day Sheet contents and the Sync payload must match the production PC. The load-bearing settings stay fixed and are guarded by a test: strict OCR engine on, oneDNN off, 4 post-09:00 exceptions per day.
- **One pinned dependency set** for the PC and the image: a hashed lockfile, Python 3.13, x86_64 only (paddlepaddle has no Linux aarch64 wheel for 3.13). `opencv-python` and `opencv-contrib-python` both at 4.10.0.84. Models are baked into the image with SHA-256 checksums, and nothing is downloaded at run time.
- **Orientation fix:** page-orientation classification runs only on images that are landscape after preprocessing. Upright portrait photos are not rotated; landscape photos are still classified.
- **Parity target:** all three Reference Set folders identical. Folder 3's expected result is the rule-based one the Owner confirmed, not the hand-edited sheet.
- **Synthetic Set:** fictional screenshots and photo variants with ground truth, deterministic per seed, usable in CI. They contain no real names or phone numbers.
- **Privacy:** the Reference Set and anything from `input_images/` or `output/` never enter the repo, the image or any output file. Parity output labels folders F1…Fn. The privacy check must print `OK` before any commit.
- **Tests:** plain functions run by `run_tests.py`, with no OCR models needed for unit tests, all new tests under 60 s. The existing 81 tests keep passing.
- **Thread counts** come from the CPUs actually available (cgroup / affinity), not the host's count. Measured: the default 10 threads made real images 2–3× slower on a laptop.

## Technical Decisions

- **Frozen core:** new code goes only in `cloud/` and `tools/`. Core modules (`main.py`, `image_preprocess.py`, `ocr.py`, `parser.py`, `session_builder.py`, `excel_writer.py`, `sheets_sync.py`) keep their names and signatures. A narrow, behaviour-preserving fix in the core (like the orientation fix) is allowed because the PC and AWS must share it. The core never imports `cloud`.
- `import runtime_env` stays the first import in every entrypoint, before numpy, cv2 or paddle.
- **Image:** AWS Lambda `python:3.13` base (Amazon Linux 2023, `dnf`), x86_64. `mesa-libGL` and `glib2` are installed via `dnf`, with headless OpenCV as the documented fallback. Env vars: `PADDLE_PDX_CACHE_HOME=/tmp/paddlex`, `PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True`.
- **Portable runner contract** (built in Epic 3): the same image runs `handler(event, context)` and `python -m cloud.runner --upload-id`.
- **Models in use:** `PP-OCRv6_medium_det` (detection), `PP-OCRv5_mobile_rec` (recognition), `PP-LCNet_x1_0_doc_ori` (page orientation). This machine resolved paddleocr 3.7.0, paddlex 3.7.2 and paddlepaddle 3.3.1.
- **Lockfile source (open):** either the production PC's `pip freeze` or this machine's 2026-10-14 set, if the Owner standardises on it. This machine's set reproduces all three references once the orientation fix is applied.

## Cross-Story Dependencies

- 1.1 feeds 1.5 (the image installs from the lockfile) and 1.6.
- 1.2 must land before 1.6 can report all three folders identical.
- 1.4 feeds Epic 2's CI gate (the Synthetic Set check blocks deploys).
- 1.3 protects Epic 3's port of the folder-processing rules into the cloud runner.
- 1.5 and 1.6 need Docker Desktop or WSL2 on the machine that builds and runs the image.
