---
title: 'Story 1.5: Linux runner image (containerise)'
type: 'feature'
created: '2026-10-14'
status: 'in-review'
route: 'dispatch'
review_loop_iteration: 0
baseline_commit: 'cde5e356ec43672dec82bf64a89fe65808117279'
context: ['{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The pipeline runs only in the PC's Windows venv. AWS needs one x86_64 Linux image that installs the pinned packages, carries the exact OCR models the PC uses, never downloads anything at run time, sizes its threads to the CPUs it is given, and contains no private file.

**Approach:**
- **Image:** a Dockerfile on the AWS Lambda `python:3.13` base, pinned by digest. It installs `requirements.lock` with `--require-hashes` and adds `mesa-libGL` and `glib2` with `dnf`.
- **Models:** baked in at build time by `tools/bake_models.py`, which downloads through the pipeline's own config and fails unless every file matches `models.sha256` (recorded from this PC).
- **Startup:** `cloud/entrypoint.sh` links the read-only models into PaddleX's writable cache home, `/tmp/paddlex`.
- **Threads:** `runtime_env` learns the CPUs actually available (affinity and cgroup quota).
- **CI:** a GitHub Actions workflow builds the image and proves it.

**Decisions:**
- Base `public.ecr.aws/lambda/python:3.13@sha256:27755d02113714211d0c49872554b3659831eccfc762a5d3fe602b27f1a2151c` (linux/amd64).
- Thread count: `paddle_cpu_threads = min(10, available CPUs)`, and the OMP/MKL defaults use the available CPUs. On the PC (16 CPUs, no cgroup) both stay exactly as today, at 10 and 16.
- The models checked are the 3 the config uses (`PP-OCRv6_medium_det`, `PP-OCRv5_mobile_rec`, `PP-LCNet_x1_0_doc_ori`). Every model file is checked except hoster metadata (`README.md`, `.gitattributes`, `.cache/`). The build fails on any mismatch, or if a model is missing or extra.
- `.dockerignore` denies everything, then allows only the runtime files. `secrets_local.py` stays excluded even when present, so the office PC can build it safely.
- CI on GitHub's hosted runner (free Actions minutes; roughly 15–25 minutes per run) runs these checks:
  - build;
  - a file check (no `secrets_local.py`, `Employees.gs`, `input_images/` or `output/`);
  - threads under `--cpus 1` report 1;
  - with `--network none --read-only --tmpfs /tmp`, `diagnose.py` and `check_synth.py` pass on a small Synthetic Set;
  - the compressed size goes in the job summary.
- No Lambda handler yet: Epic 3 adds it as `CMD`.
- Done when the workflow passes on GitHub, `run_tests.py` passes locally, and this story's commit is pushed to `main` with the Owner's approval.

## Boundaries & Constraints

**Always:** `import runtime_env` stays first in every entrypoint. The PC's OCR settings and results don't change. Models are verified before they are baked. Only `/tmp` is written at run time.

**Never:** no model download at run time; no credentials or AWS access in this workflow; no `COPY . .`; no edits to `ocr.py`, `parser.py`, `session_builder.py`, `excel_writer.py` or `sheets_sync.py`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| PC | Windows, 16 CPUs, no cgroup | OMP 16, paddle threads 10, as today | N/A |
| Container quota | `--cpus 1` on a 2-vCPU host | OMP 1, paddle threads 1 | N/A |
| Fractional quota | cgroup `cpu.max` = `150000 100000` | 2 (rounded up) | `max` or unreadable means no limit |
| Offline, read-only | `--network none --read-only --tmpfs /tmp` | OCR reads the Synthetic Set | Fails loudly if a model is missing |
| Model drift | Downloaded file differs from `models.sha256` | Build fails, naming the file | Maintainer re-records on purpose |
| Secrets on the build machine | `secrets_local.py` beside the Dockerfile | Not in the image | File check fails the job if it is |

</frozen-after-approval>

## Code Map

- `runtime_env.py:34` -- `configure()` sets OMP/MKL defaults from `os.cpu_count()`; add `available_cpus()` and use it.
- `config.py:97` -- `paddle_cpu_threads = 10`; becomes `min(10, available_cpus())` via `field(default_factory=...)`.
- `ocr.py:109` -- `_build_paddle_engine()` builds from `CONFIG`; `bake_models.py` calls it to download exactly the models in use.
- PaddleX: `paddlex/utils/cache.py:29` (`PADDLE_PDX_CACHE_HOME`, writes `locks/`, `temp/`, `func_ret/`); `official_models.py:930` (an existing model folder is used with no network and no writes).
- `diagnose.py`, `tools/check_synth.py`, `tools/gen_synth.py` -- the smoke tests; fonts for generation are installed on the runner, not in the image.

## Tasks & Acceptance

**Execution:**
- [x] `runtime_env.py`, `config.py` -- `available_cpus()` (min of `cpu_count`, affinity, cgroup v2/v1 quota); thread defaults from it -- AC threads.
- [x] `tools/bake_models.py` -- `--write-manifest` (from a cache) and `--bake DEST` (download via config, verify, copy 0444, check the model set) -- AC models.
- [x] `models.sha256` -- the 11 checksums recorded from this PC.
- [x] `cloud/entrypoint.sh` -- link models into `$PADDLE_PDX_CACHE_HOME/official_models`, then `exec "$@"`.
- [x] `Dockerfile`, `.dockerignore` -- as decided; layer order puts the model step before the code copy, so code edits don't re-download models.
- [x] `.github/workflows/runner-image.yml` -- build, file check, threads, offline smoke, size summary; `permissions: contents: read`.
- [x] `tests.py` -- cgroup parsing, `available_cpus` with mocks, manifest verification (match, mismatch, missing, extra).
- [x] `docs/TECHNICAL.md` -- "Runner image" section: build, run, updating models.

**Acceptance Criteria:**
- Given the workflow on GitHub, when it runs, then every step passes and the summary shows the compressed size.
- Given `run_tests.py` on the PC, when it runs, then all tests pass and `CONFIG.paddle_cpu_threads == 10`.

## Verification

**Commands:**
- `.venv\Scripts\python.exe -X utf8 run_tests.py` -- expected: 0 failed.
- `python tools/bake_models.py --write-manifest` vs `models.sha256` -- expected: identical.
- GitHub Actions "Runner image" run -- expected: green.

## Implementation Notes

- CI run 1 (`2b76d7d`, run 38596121659) failed at the model step: `ImportError: libgomp.so.1`. paddlepaddle needs GNU OpenMP, which Amazon Linux's minimal image lacks. The system packages, the hash-checked `pip install` and the earlier layers all passed. The fix adds `libgomp` to the `dnf` line, plus a build check that runs `ldd` over the paddle and OpenCV native libraries and lists every missing library at once, before the slow model step. The frozen decision named only `mesa-libGL` and `glib2`; the purpose (the system libraries the wheels need) is unchanged.

## Review Triage Log

Inline review at the Owner's choice; the three layers (blind hunter, edge cases, verification gaps) were applied in this session.

- medium (patched): the "no private files" step wrapped `docker run` in `|| true`, so a container that failed to run would pass the check. Now `true` ends the script inside the container; a docker failure fails the step.
- low (patched): CI ran the container as root, but Lambda runs as a non-root user. The offline OCR step now uses `--user 1000:1000`, so a permission problem shows up before Lambda.
- low (patched): nothing checked Lambda's 10 GB container-image limit. The size step now fails above it.
- low (deferred): actions are pinned by tag, not commit SHA. Not urgent while the workflow holds no secrets; scheduled with Story 2.2 in `deferred-work.md`.
- low (rejected): re-running `--bake` into the same folder on Windows fails on the read-only files. The image build always bakes into a fresh layer; reruns to the same folder aren't a supported use.
- false: "the entrypoint could fail on an existing link." `-e` is true for a working link, so the step is skipped. Only a dangling link (the models missing from the image) fails, and failing loudly is correct there.
- false: "`.dockerignore` re-includes `secrets_local.py` via `!*.py`." The later `secrets_local.py` rule wins (last match). The decoy step in CI proves it on every run.
