---
title: calls-log AWS migration — risk assessment
step: 2 (Phase 5 risk assessment)
author: Winston (bmad-agent-architect), using bmad-review lenses adversarial, edge-case-hunter, verification-gap
date: 2026-10-14
status: complete — input to Step 3 (PRD)
---

# calls-log AWS migration: risk assessment

## How this was run

- **Method (owner-approved, question 5):** the TEA module is not installed. Winston ran three
  `bmad-review` lenses: `adversarial` (≥ 10 findings by design), `edge-case-hunter` (unhandled
  paths in the code) and `verification-gap` (behaviour the migration changes that no test would
  catch). They ran one after another in one session, each without seeing the others' findings.
- **Content reviewed:** `brownfield-system-documentation.md`, `project-context.md`,
  `ocr-benchmark-2026-10-14.md`, plus the code they cite.
- **Kept in mind throughout:** the $2/month hard cap, `ap-southeast-1`, ≤ 2 h turnaround,
  per-upload runs, Apps Script unchanged on Google, no employee data in tracked files.
- **External facts checked:** PaddleX reads `PADDLE_PDX_CACHE_HOME` and writes `func_ret/` and
  `locks/` under it (`.venv/Lib/site-packages/paddlex/utils/cache.py:28-38`). It checks
  connectivity to HuggingFace, AIStudio, ModelScope and BOS unless
  `PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK` is set (`paddlex/inference/utils/official_models.py:880-921`,
  `paddlex/utils/flags.py:72-74`).
- **Lens output:** findings carry no severity, as the lenses require. Winston's synthesis below
  groups them into the owner's three categories. Where lenses overlap, the overlap is
  cross-referenced rather than merged.

---

## Synthesis

### Deployment blockers: AWS cannot run the system unchanged until each is resolved

| ID | Blocker | Findings |
|---|---|---|
| B1 | **OCR parity is unprovable.** Unpinned packages pulled a different detector (`PP-OCRv6_medium_det`); production's versions are unknown; no test exercises OCR | ADV-01, VG-01 |
| B2 | **No "folder upload finished" signal.** Per-object S3 events would OCR half-uploaded folders and write short days to the Sheet | ADV-02, EC-06 |
| B3 | **The filesystem rename that acts as the queue has no S3 equivalent.** "Archive only if xlsx and sync succeeded" and retry-by-staying-put must be redesigned | ADV-03, VG-02 |
| B4 | **Credentials are a Python import.** They would be baked into the container image unless `config.py` gains an env/SSM path | ADV-08, VG-05 |
| B5 | **Lambda filesystem and network:** PaddleX needs a writable cache/lock directory and does a connectivity check at start-up; only `/tmp` is writable | ADV-07 |
| B6 | **OpenCV on Linux:** PaddleX pins the GUI build `opencv-contrib-python==4.10.0.84`, which needs `libGL`/`glib` in the image | ADV-11, EC-15 |
| B7 | **Unbounded retries vs the $2 cap:** a failing folder is re-OCR'd on every run, and AWS budgets react hours late | ADV-05, ADV-06, EC-01, EC-07 |
| B8 | **No operator upload path:** a non-technical operator has no defined, safe way to get about 2,000 images a week into S3 | ADV-13 |

### Security findings

| ID | Finding | Findings |
|---|---|---|
| S1 | The Web App is open to "Anyone" behind one shared secret, held as a literal in the script and sent in the body with no replay protection. The migration copies it to a new environment | ADV-09 |
| S2 | Screenshots hold customer phone numbers. The S3 baseline (Block Public Access, encryption, TLS-only, 30-day lifecycle, versioning for outputs) is not yet specified | ADV-10 |
| S3 | CloudWatch Logs keep folder names (employee names) and dates **forever** by default | ADV-12 |
| S4 | The privacy gate `check_private_data.py` silently skips its value scan when the private files are absent, which will always be the case in CI | VG-06 |
| S5 | Supply chain: no lockfile or hashes, a large transitive tree (modelscope, aistudio-sdk, huggingface-hub, bce-python-sdk), and model weights downloaded without pinned checksums | ADV-14 |
| S6 | The GitHub OIDC trust policy must be scoped to the repo and branch, or any repo could assume the deploy role | ADV-15 |

### Cloud-readiness smells

| ID | Smell | Findings |
|---|---|---|
| C1 | Global mutable `CONFIG` leaks state across warm Lambda invocations | EC-11 |
| C2 | Thread counts come from host CPUs (`os.cpu_count()`, fixed `paddle_cpu_threads = 10`), not the container's quota | ADV-16, EC-12 |
| C3 | `(n)` output naming, archive moves and Apps Script tab creation all race under parallel workers | EC-03, EC-04, EC-09 |
| C4 | Cost-model headroom is overstated: laptop wall time, a free tier shared across the account, and INIT is billed | ADV-04 |
| C5 | Dual-running the PC and AWS during rollback double-writes the Sheets | ADV-17 |
| C6 | `resync_output.py` disagrees with `main.py` on filenames and on which sheet to read | EC-07b, EC-08, VG-07 |
| C7 | Retention behaviour changes: local mtime-based pruning vs S3 lifecycle from object creation | EC-05 |
| C8 | The AWS **Free plan** closes the account after 6 months | ADV-18 |

### What Step 3 (PRD) must carry as requirements

1. A **parity gate:** golden comparison against the owner's PC-produced reference outputs, run
   locally on real images that never leave the PC. Pass/fail is identical sessions per folder.
2. **Pinned runtime:** a lockfile generated from the production PC's `pip freeze`, the same model
   names, and models baked into the image.
3. **Folder-completion contract** for uploads, plus **single-flight per (name, date).**
4. **Cost guardrails** that bound the worst-case spend before the budget alert can react.
5. **Security baseline:** secret rotation at cutover, an SSM SecureString, the S3 baseline, log
   retention, and a scoped OIDC role.
6. **Operator upload UX** that needs no long-lived AWS keys on the PC.
7. **Rollback:** only one system syncs at a time. The PC stays as a shadow (sync off) for 4 weeks.

---

## Lens reports

### Adversarial (18 findings)

**ADV-01** · `project-context.md` → "Unpinned versions change the OCR models"; `requirements.txt:2-21`; `config.py:54-65`
- *Problem:* "No functional change" is promised, but the runtime's OCR models and versions are
  not pinned to production. A fresh install already selects a different detector.
- *Fix:* Generate a lockfile from the production PC's `pip freeze`. Pin the same model names
  (explicitly via `paddle_det_model_name`/`paddle_rec_model_name` if production's defaults
  differ). Bake the models into the image. Gate releases on golden parity.
- *Consequence:* AWS returns different call rows from the PC, and employees' daily totals change
  silently.

**ADV-02** · `brownfield-system-documentation.md` §6 ("Filesystem as queue"); constraint 2 (per-upload runs)
- *Problem:* Per-upload triggers fire per object. Nothing defines when a folder's upload is
  complete.
- *Fix:* Add a completion contract: upload a `_READY` marker (or a manifest with an image count)
  last, and trigger only on that key. Alternatively, debounce until no new objects arrive for N
  minutes.
- *Consequence:* A half-uploaded folder is OCR'd, synced and archived. The Sheet shows a short day
  that looks valid.

**ADV-03** · `main.py:196-217`, `main.py:307-320`
- *Problem:* "Done" is a same-volume rename. S3 has no atomic rename, so copy-then-delete can
  stop half-way.
- *Fix:* Don't move objects. Record per-folder state (`pending` / `done` / `failed`, with the xlsx
  key and sync result) in a status object, using conditional writes. Let lifecycle rules handle
  retention.
- *Consequence:* A folder split between the inbox and the archive gets re-processed from partial
  images.

**ADV-04** · `ocr-benchmark-2026-10-14.md` → cost table
- *Problem:* "Stays free up to ~2.1× slower" uses laptop wall time on a 4.9 GHz-class core. AWS
  vCPUs are slower, INIT is billed, and the 400,000 GB-s allowance is shared by every function in
  the account.
- *Fix:* Re-measure inside the real runtime (Lambda 2,048 MB, then Fargate 1 vCPU) on the
  synthetic set plus the real reference set. Record headroom as measured, not extrapolated.
- *Consequence:* The design is chosen on a free-tier assumption that real speed then breaks.

**ADV-05** · `project-context.md` → "AWS has no true hard cap"
- *Problem:* The worst-case overrun is not quantified. Budgets refresh only up to three times a
  day. One runaway 2 GB Lambda costs about $0.12/hour beyond the free tier ($0.0000166667 ×
  7,200 GB-s).
- *Fix:* Bound spend structurally:
  - reserved concurrency of 1–2;
  - `maxReceiveCount` plus a dead-letter queue;
  - a function timeout;
  - a CloudWatch alarm on invocation count or duration, which fires in minutes, not hours;
  - the budget action as the last line of defence.
- *Consequence:* A retry loop at concurrency 2 can spend about $2 inside one budget-refresh gap.

**ADV-06** · `main.py:273-280`, `main.py:290-320`; `docs/TECHNICAL.md:400-401`
- *Problem:* A failing folder (unknown name, zero sessions, sync down) stays in the inbox and is
  retried on every run. Event-driven, that becomes an unbounded OCR loop.
- *Fix:* Put a dead-letter queue after N attempts and alert the operator. Validate the folder name
  against the allowed list *before* OCR. The xlsx is still produced, so behaviour is unchanged.
- *Consequence:* One misnamed folder burns compute until the $2 cap is hit.

**ADV-07** · Lambda runtime vs `paddlex/utils/cache.py:28-38`, `official_models.py:880-921`
- *Problem:* PaddleX writes its cache and lock directories under `~/.paddlex` and checks
  connectivity to four model hosts. In Lambda only `/tmp` is writable.
- *Fix:* Bake the models into the image. At cold start, set `PADDLE_PDX_CACHE_HOME=/tmp/paddlex`
  and copy or link the models there (or pass explicit model dirs). Set
  `PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True`.
- *Consequence:* Every cold start fails, or re-downloads about 82 MB of models from the internet.

**ADV-08** · `config.py:15-28`
- *Problem:* Credentials arrive only via `import secrets_local`. Containerising "as is" means
  copying that file into the image.
- *Fix:* Add an env-var path (filled from an SSM Parameter Store SecureString at runtime) that
  takes priority, with `secrets_local.py` kept as the fallback for the PC. Never `COPY` it in the
  Dockerfile, and add it to `.dockerignore`.
- *Consequence:* Anyone with ECR pull access, or a leaked image, gets write access to every
  employee's Sheet.

**ADV-09** · `apps_script/WebAppSync.gs:24-36`, `:105`; `apps_script/README_SETUP.md:26-34`
- *Problem:* The single shared secret is a script literal, sent in the body, compared with `!==`,
  and never expires. The migration adds AWS as another holder of it.
- *Fix:* Rotate it at cutover and move it to Script Properties, as the file itself suggests at
  :33-35. Store the client copy only in SSM. Optionally add an HMAC of body+timestamp later
  (Apps Script change, post-migration).
- *Consequence:* A leaked secret allows silent overwrites of any employee's day tabs.

**ADV-10** · `brownfield-system-documentation.md` §4 (Inbox/Archive sensitivity: PII)
- *Problem:* No S3 data-protection baseline is defined for customer phone numbers and employee
  hours.
- *Fix:* Block Public Access on (account and bucket), default SSE-S3, a bucket policy denying
  non-TLS, a 30-day lifecycle on images, versioning plus a never-expire rule for outputs (owner
  1f), and access logging only if it fits the cap.
- *Consequence:* A misconfiguration exposes customers' numbers. Lost outputs breach the owner's
  "never lose outputs" rule.

**ADV-11** · `requirements.txt:18-21`; paddlex requires `opencv-contrib-python==4.10.0.84`
- *Problem:* Both OpenCV wheels are GUI builds. On slim Linux or Lambda base images,
  `import cv2` fails without `libGL.so.1`/`libglib`.
- *Fix:* Install `mesa-libGL` and `glib2` (AL2023) or `libgl1` and `libglib2.0-0` (Debian) in the
  image. Add a smoke test that runs `diagnose.py` on a synthetic image during the image build.
- *Consequence:* Every image fails OCR on AWS. Folders are kept and retried (see ADV-06).

**ADV-12** · `main.py:97`, `main.py:259`, `main.py:302-305`
- *Problem:* Logs carry folder names (employee names) and dates. CloudWatch log groups never
  expire by default.
- *Fix:* Set retention to match the 30-day image rule, in Terraform. Keep logs free of OCR row
  text; `--debug` must stay off in AWS.
- *Consequence:* Personal data is retained indefinitely, and log storage slowly eats the cap.

**ADV-13** · `brownfield-system-documentation.md` §9 (intake is manual); owner 1h (non-technical operator)
- *Problem:* No defined way for the operator to put about 2,000 images a week into S3 with the
  right folder names.
- *Fix:* Decide in Step 4 between an upload page using presigned URLs (with name/date validation)
  and a scripted `aws s3 sync` through IAM Identity Center short-lived credentials. Never use
  long-lived access keys on the PC.
- *Consequence:* The operator stalls, or long-lived keys end up on a shared PC.

**ADV-14** · `requirements.txt` (lower bounds only); paddlex transitive dependencies
- *Problem:* No lockfile or hashes. The large transitive tree includes model-hub SDKs, and the
  weights are fetched without pinned checksums.
- *Fix:* Use `pip-compile --generate-hashes` or `uv lock` from the production freeze; install with
  `--require-hashes`; record the SHA-256 of each baked model; set up Dependabot/Renovate with a
  parity gate.
- *Consequence:* Non-reproducible builds, and a compromised dependency runs with the job's IAM
  role.

**ADV-15** · Phase 7 plan (GitHub OIDC → ECR), not yet designed
- *Problem:* An OIDC role whose trust policy isn't pinned to the repo and branch can be assumed
  from other repos or branches.
- *Fix:* Pin the trust condition `token.actions.githubusercontent.com:sub` =
  `repo:<owner>/<repo>:ref:refs/heads/main` and `aud` = `sts.amazonaws.com`. Limit permissions to
  pushing to one ECR repo and updating one function.
- *Consequence:* A forked or other repo deploys arbitrary code with access to PII and the secret.

**ADV-16** · `runtime_env.py:51-52`; `config.py:94-95`
- *Problem:* OMP/MKL threads use `os.cpu_count()` (the host's count) and paddle uses a fixed 10
  threads. Lambda and Fargate give 1–2 vCPUs.
- *Fix:* Set `OMP_NUM_THREADS`/`MKL_NUM_THREADS` from the task size (runtime_env already defers to
  existing env values) and derive `paddle_cpu_threads` from `len(os.sched_getaffinity(0))` on
  Linux.
- *Consequence:* Oversubscription. The benchmark already shows 8-thread runs spiking to 90–100
  CPU-seconds per image.

**ADV-17** · Owner 1j (PC kept as fallback 4 weeks); `config.py:184`
- *Problem:* If both the PC and AWS process the same day with sync on, the last writer wins in the
  Sheet. Version drift makes them disagree.
- *Fix:* During shadow mode the PC runs with `sheets_sync_enabled = False` and its outputs are
  compared with AWS's. The cutover runbook flips exactly one system to sync.
- *Consequence:* Sheet values flip-flop between two engines, and nobody can tell which is right.

**ADV-18** · `project-context.md` (account default 1g)
- *Problem:* New AWS accounts on the **Free plan** close automatically after 6 months or when the
  credits run out.
- *Fix:* Choose the **Paid plan** at sign-up; credits still apply. Record the decision in the PRD.
- *Consequence:* Production stops at month 6 and the account's data is at risk.

### Edge-Case Hunter (16 findings)

```json
[
 {"lens":"edge-case-hunter","id":"EC-01","location":"main.py:253-257","trigger_condition":"Folder with >40 or zero supported images returns None; counted skipped, exit 0","guard_snippet":"except SystemExit as exc: failures.append((sub.name, str(exc))); return None  # surface as failure, not skip","potential_consequence":"41-image or HEIC-only folder silently never processes; no alert"},
 {"lens":"edge-case-hunter","id":"EC-02","location":"main.py:170-171","trigger_condition":"Impossible calendar dates like 2026-02-30 accepted and sent to sync","guard_snippet":"datetime.date(y, mo, d)  # raises ValueError -> return None","potential_consequence":"Sheet gets a bogus day tab; month link silently missing"},
 {"lens":"edge-case-hunter","id":"EC-03","location":"main.py:183-193","trigger_condition":"Two workers check-then-write the same (n) xlsx name concurrently","guard_snippet":"s3.put_object(..., IfNoneMatch='*')  # retry next n on PreconditionFailed","potential_consequence":"One worker overwrites the other's output file"},
 {"lens":"edge-case-hunter","id":"EC-04","location":"main.py:213-216","trigger_condition":"Archive target and same-second timestamped target both exist","guard_snippet":"target = archive_root / f'{name} ({datetime.now():%Y%m%d-%H%M%S-%f})'","potential_consequence":"shutil.move nests the folder inside the existing archive folder"},
 {"lens":"edge-case-hunter","id":"EC-05","location":"main.py:228-234","trigger_condition":"Archived folder whose contents are older than 30 days pruned same run","guard_snippet":"os.utime(target)  # after shutil.move, so retention starts at archive time","potential_consequence":"Backfilled source images deleted minutes after processing"},
 {"lens":"edge-case-hunter","id":"EC-06","location":"main.py:241-262","trigger_condition":"Folder still receiving files when gather_jpg_files lists it","guard_snippet":"if not (folder / '_READY').exists(): return None  # wait for completion marker","potential_consequence":"Partial day synced and archived as if complete"},
 {"lens":"edge-case-hunter","id":"EC-07","location":"main.py:273-280, main.py:315-320","trigger_condition":"Persistently failing folder retried every run with no attempt limit","guard_snippet":"if attempts(folder) >= MAX: move_to('_failed'); alert(); return","potential_consequence":"Unbounded repeated OCR cost on one poison folder"},
 {"lens":"edge-case-hunter","id":"EC-07b","location":"resync_output.py:47 vs main.py:158-177","trigger_condition":"Unpadded date or missing ' & ' synced by main, skipped by resync","guard_snippet":"reuse main.parse_name_date(path.stem) instead of FILENAME_RE","potential_consequence":"Backfill silently skips days main.py synced normally"},
 {"lens":"edge-case-hunter","id":"EC-08","location":"resync_output.py:75","trigger_condition":"Reads workbook active sheet; writer uses CONFIG.sheet_name","guard_snippet":"wb = load_workbook(p); ws = wb[CONFIG.sheet_name] if CONFIG.sheet_name in wb.sheetnames else wb.worksheets[0]","potential_consequence":"Resync reads wrong sheet if template active tab differs"},
 {"lens":"edge-case-hunter","id":"EC-09","location":"apps_script/WebAppSync.gs:131-169","trigger_condition":"Concurrent POSTs for same person/date both miss tab then insert","guard_snippet":"AWS side: SQS FIFO MessageGroupId = norm(name)+'|'+date  # single-flight, no Apps Script change","potential_consequence":"Duplicate day tabs; month link points at one arbitrarily"},
 {"lens":"edge-case-hunter","id":"EC-10","location":"apps_script/WebAppSync.gs:180-184","trigger_condition":"Session start/end not validated; malformed values become NaN","guard_snippet":"if (!/^\\d{2}:\\d{2}$/.test(se.start) || !/^\\d{2}:\\d{2}$/.test(se.end)) return json_({ok:false,error:'bad time'})","potential_consequence":"Day tab cells show NaN; totals break without error"},
 {"lens":"edge-case-hunter","id":"EC-11","location":"main.py:426-431, resync_output.py:131, ocr.py:262","trigger_condition":"Global CONFIG mutations persist across warm-container invocations","guard_snippet":"cfg = dataclasses.replace(CONFIG, **overrides)  # per-invocation copy passed down","potential_consequence":"One invocation's flags silently change later invocations' behaviour"},
 {"lens":"edge-case-hunter","id":"EC-12","location":"runtime_env.py:51-52, config.py:95","trigger_condition":"Thread counts from host CPUs, ignoring container CPU quota","guard_snippet":"n = len(os.sched_getaffinity(0)) if hasattr(os, 'sched_getaffinity') else os.cpu_count()","potential_consequence":"Thread oversubscription; slower, costlier OCR per image"},
 {"lens":"edge-case-hunter","id":"EC-13","location":"sheets_sync.py:63-85","trigger_condition":"No overall deadline: 3x60s timeouts plus backoff per folder","guard_snippet":"deadline = time.monotonic() + budget_s; stop retrying when deadline passes","potential_consequence":"Runtime timeout kills job after xlsx written, before state recorded"},
 {"lens":"edge-case-hunter","id":"EC-14","location":"main.py:161-177","trigger_condition":"Same person-day uploaded as NFC and NFD names","guard_snippet":"name = unicodedata.normalize('NFC', name); key = unicodedata.normalize('NFC', folder_name)","potential_consequence":"Two outputs for one day; resync syncs whichever sorts last"},
 {"lens":"edge-case-hunter","id":"EC-15","location":"image_preprocess.py:46-52","trigger_condition":"cv2 ImportError (e.g. missing libGL) silently disables rectification","guard_snippet":"raise RuntimeError('OpenCV required') if cv2 is None and not CONFIG.allow_no_opencv","potential_consequence":"Photos OCR'd unrectified if OCR engine still loads"}
]
```

### Verification Gap (7 findings)

Scope: the behaviour the migration changes (runtime OS, dependency versions, config source, I/O
layer, trigger). Evidence: one test file exists (`tests.py`, 41 test functions; search over the
repo excluding `.venv`, `.claude`, `_bmad`). Searching `tests.py` for `write_sessions`,
`excel_writer`, `_process_one_folder`, `_run_batch`, `_archive_or_delete`, `_prune_archive`,
`_next_available_xlsx`, `parse_name_date`, `gather_jpg_files`, `extract_calls`,
`image_preprocess`, `preprocess(`, `run_ocr`, `group_lines_into_rows`, `resync_output`,
`read_sessions`, `collect_files`, `check_private_data`, `paddle_enable_mkldnn`,
`ocr_strict_engine` and `runtime_env` returned **no matches**. The sync tests read are at
`tests.py:419-539`.

**VG-01** · regression-gap · `ocr.py:109-153` (engine/model selection), `image_preprocess.py:428-471`
- *Gap:* Nothing runs preprocessing or OCR. A change of OS, packages or detector is unverified.
- *Missing check:* A golden parity test: for each reference folder, the sessions from
  `extract_calls → dedupe → build_sessions` must equal the PC's reference xlsx B7:C22. Run it on
  the owner's real set locally, and on the synthetic set (`gen_synth.py` ground truth) in CI.
- *Consequence:* With `PP-OCRv6_medium_det` dropping a faint "(38秒)", rows become 0-second calls
  and session spans shrink. No test fails.
- *Consumer:* `main.extract_calls` at `main.py:80-155`, feeding every Sheet.
- *Evidence:* no test references `preprocess(`, `run_ocr` or `group_lines_into_rows` (search above).

**VG-02** · regression-gap · `main.py:241-322`, `main.py:325-376`
- *Gap:* The semantics the S3 port must preserve are unpinned: archive only if the xlsx was
  written and sync succeeded, refuse on zero sessions, skip on > 40 images, `(n)` naming, skip
  `_processed`.
- *Missing check:* Characterisation tests for `_process_one_folder`/`_run_batch` using temp dirs,
  with `extract_calls` and `sync_day` mocked: (a) sync failure means the folder stays and the
  xlsx exists; (b) zero sessions raises and nothing is written; (c) success archives the folder.
- *Consequence:* The port archives a folder after a failed sync. Images expire and the day never
  reaches the Sheet. No test fails.
- *Consumer:* batch entry `main.py:438-444`.
- *Evidence:* no test references these functions (search above).

**VG-03** · regression-gap · `excel_writer.py:37-122`
- *Gap:* xlsx contents (day-fraction values, `=C-B` spans, `=SUM(D7:D22)`, B3, row expansion
  past 16) are unverified across openpyxl and OS changes.
- *Missing check:* Write sessions into `sprs.xlsx` (including 17 sessions), reload, and assert the
  B/C values, D formulas, B2 range covering the expanded rows, B3 and number formats.
- *Consequence:* An expanded day's B2 omits rows, so the owner's totals are wrong. No test fails.
- *Consumer:* `main._process_one_folder` at `main.py:284`; `resync_output.read_sessions` at
  `resync_output.py:73-94`.
- *Evidence:* no test references `write_sessions` or `excel_writer` (search above).

**VG-04** · regression-gap · `config.py:50`, `config.py:93` (load-bearing defaults)
- *Gap:* When config gains env-var overrides for AWS, the parsing of `ocr_strict_engine` and
  `paddle_enable_mkldnn` is unverified (for example, `bool("False")` is True).
- *Missing check:* Assert the defaults (`ocr_strict_engine is True`, `paddle_enable_mkldnn is
  False`), plus env-parsing tests showing "false", "0" and "" map to False.
- *Consequence:* Silent engine fallback or oneDNN gets re-enabled in AWS, which are exactly the
  two incidents `AGENTS.md` records.
- *Consumer:* `ocr.run_ocr` at `ocr.py:395-440`; `ocr._build_paddle_engine` at `ocr.py:145`.
- *Evidence:* no test references `ocr_strict_engine` or `paddle_enable_mkldnn` (search above).

**VG-05** · regression-gap · `config.py:23-28` (credential loading)
- *Gap:* Tests patch `CONFIG` directly (`tests.py:444-450`), so the loader that the migration
  replaces is never exercised.
- *Missing check:* Unit tests for the new loader: env set overrides `secrets_local`; neither set
  gives empty values; and a check that the secret never appears in log output.
- *Consequence:* A wrong env name ships, every sync fails with "sheets_webapp_url is empty", and
  every folder is retried. That failure is loud only after deployment.
- *Consumer:* `sheets_sync.sync_day` at `sheets_sync.py:41-52`.
- *Evidence:* `tests.py:439-459` sets `sheets_webapp_url`/`sheets_sync_secret` via
  `mock.patch.object`; there is no test of `config` import behaviour.

**VG-06** · broken-verification-gap · `tools/check_private_data.py:115-123`
- *Gap:* The privacy gate looks like it verifies "no employee data". When `secrets_local.py` and
  `Employees.gs` are missing (always so in GitHub Actions), it skips the value scan, prints a
  note and exits 0.
- *Missing check:* In CI, fail closed unless a private denylist is supplied (for example, hashed
  names and IDs from a GitHub secret). Add a test that a planted fake value with the denylist
  present makes it exit 1.
- *Consequence:* A real employee name or spreadsheet ID committed in a doc passes CI and gets
  published.
- *Consumer:* `.githooks/pre-commit`, `.githooks/pre-push`, and the planned CI job.
- *Evidence:* no test references `check_private_data`; the code at :115-117 only prints a note.

**VG-07** · regression-gap · `resync_output.py:73-111`
- *Gap:* The backfill tool will move to read S3 outputs. Its filename matching and sheet
  selection are untested and already diverge from `main.py` (EC-07b, EC-08).
- *Missing check:* Tests for `collect_files` (base vs `(n)` preference, unpadded dates) and for
  `read_sessions` (expanded rows, stop rule).
- *Consequence:* A repair run after an outage silently skips days, and the owner believes the
  Sheets are complete.
- *Consumer:* the owner's recovery runbook (`docs/TECHNICAL.md:120-132`).
- *Evidence:* no test references `resync_output`, `read_sessions` or `collect_files` (search above).

---

## Overlaps worth noticing

- **Retry loop vs the cap:** ADV-05, ADV-06, EC-07, EC-01 (silent skip is the mirror image). This
  is the single largest cost risk.
- **Parity:** ADV-01, ADV-11, VG-01, VG-04, ADV-16. These are five independent ways AWS output
  could differ from the PC.
- **Concurrency:** ADV-02, EC-03, EC-04, EC-06, EC-09. All are hidden today because one process
  runs sequentially.

## Findings JSON (adversarial and verification-gap)

```json
[
 {"lens":"adversarial","id":"ADV-01","location":"project-context.md; requirements.txt:2-21; config.py:54-65","trigger_condition":"OCR models/versions not pinned to production; fresh install selects PP-OCRv6_medium_det","guard_snippet":"Lockfile from production pip freeze; pin model names; bake models; golden parity gate","potential_consequence":"AWS call rows differ from PC; daily totals change silently"},
 {"lens":"adversarial","id":"ADV-02","location":"brownfield-system-documentation.md §6; constraint 2","trigger_condition":"Per-object S3 events with no folder-complete contract","guard_snippet":"Trigger only on a last-uploaded _READY/manifest object, or debounce N minutes","potential_consequence":"Half-uploaded day synced and archived as complete"},
 {"lens":"adversarial","id":"ADV-03","location":"main.py:196-217, 307-320","trigger_condition":"Rename-as-commit has no atomic S3 equivalent","guard_snippet":"Per-folder state object with conditional writes; lifecycle for retention; no object moves","potential_consequence":"Folder split between inbox/archive is reprocessed partially"},
 {"lens":"adversarial","id":"ADV-04","location":"ocr-benchmark-2026-10-14.md cost table","trigger_condition":"Free-tier headroom extrapolated from laptop wall time; shared allowance; INIT billed","guard_snippet":"Re-measure in Lambda 2,048 MB and Fargate 1 vCPU on synthetic + real sets","potential_consequence":"Design chosen on a free-tier assumption real speed breaks"},
 {"lens":"adversarial","id":"ADV-05","location":"project-context.md hard-cap note","trigger_condition":"Worst-case overrun unquantified; budgets lag up to ~8 h; $0.12/h per 2 GB Lambda","guard_snippet":"Reserved concurrency 1-2, DLQ+maxReceiveCount, timeout, CloudWatch alarm, budget action last","potential_consequence":"Retry loop spends the $2 cap inside one refresh gap"},
 {"lens":"adversarial","id":"ADV-06","location":"main.py:273-280, 290-320; docs/TECHNICAL.md:400-401","trigger_condition":"Failing folders retried forever; event-driven makes it a loop","guard_snippet":"DLQ after N attempts + alert; validate name before OCR","potential_consequence":"One misnamed folder burns compute to the cap"},
 {"lens":"adversarial","id":"ADV-07","location":"paddlex/utils/cache.py:28-38; official_models.py:880-921","trigger_condition":"PaddleX needs writable ~/.paddlex and probes model hosts; Lambda only /tmp writable","guard_snippet":"Bake models; PADDLE_PDX_CACHE_HOME=/tmp/paddlex; PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True","potential_consequence":"Cold starts fail or re-download ~82 MB each time"},
 {"lens":"adversarial","id":"ADV-08","location":"config.py:15-28","trigger_condition":"Credentials only via import secrets_local; container would bake it in","guard_snippet":"Env/SSM path first, secrets_local fallback; .dockerignore it","potential_consequence":"Image leak grants write access to every employee's Sheet"},
 {"lens":"adversarial","id":"ADV-09","location":"apps_script/WebAppSync.gs:24-36,105; README_SETUP.md:26-34","trigger_condition":"Single literal shared secret, no expiry/replay protection, new holder added","guard_snippet":"Rotate at cutover; Script Properties; client copy only in SSM; HMAC later","potential_consequence":"Leaked secret allows silent overwrite of any day tab"},
 {"lens":"adversarial","id":"ADV-10","location":"brownfield-system-documentation.md §4","trigger_condition":"No S3 protection baseline for PII","guard_snippet":"BPA, SSE-S3, TLS-only policy, 30-day image lifecycle, versioned never-expire outputs","potential_consequence":"Customer numbers exposed; outputs lost against owner rule"},
 {"lens":"adversarial","id":"ADV-11","location":"requirements.txt:18-21; paddlex opencv-contrib-python==4.10.0.84","trigger_condition":"GUI OpenCV wheels need libGL/glib on Linux","guard_snippet":"Install mesa-libGL+glib2 (AL2023) or libgl1+libglib2.0-0; build-time diagnose.py smoke test","potential_consequence":"Every image fails OCR on AWS; endless retries"},
 {"lens":"adversarial","id":"ADV-12","location":"main.py:97, 259, 302-305","trigger_condition":"Employee names in logs; CloudWatch default retention never expires","guard_snippet":"Terraform log retention 30 days; --debug off","potential_consequence":"PII retained indefinitely; storage creeps into the cap"},
 {"lens":"adversarial","id":"ADV-13","location":"brownfield-system-documentation.md §9; owner 1h","trigger_condition":"No defined operator upload path for ~2,000 images/week","guard_snippet":"Presigned-URL upload page with validation, or Identity Center + scripted sync; no long-lived keys","potential_consequence":"Operator stalls or long-lived keys land on a shared PC"},
 {"lens":"adversarial","id":"ADV-14","location":"requirements.txt","trigger_condition":"No lockfile/hashes; large transitive tree; unpinned model checksums","guard_snippet":"Hashed lockfile from production freeze; --require-hashes; model SHA-256","potential_consequence":"Non-reproducible builds; compromised dependency runs with job role"},
 {"lens":"adversarial","id":"ADV-15","location":"Phase 7 OIDC plan","trigger_condition":"OIDC trust not pinned to repo/branch","guard_snippet":"sub = repo:<owner>/<repo>:ref:refs/heads/main; aud = sts.amazonaws.com; minimal perms","potential_consequence":"Other repos/branches deploy code with PII access"},
 {"lens":"adversarial","id":"ADV-16","location":"runtime_env.py:51-52; config.py:94-95","trigger_condition":"Threads from host CPU count; fixed 10 paddle threads on 1-2 vCPU","guard_snippet":"Env-set OMP/MKL from task size; paddle_cpu_threads from sched_getaffinity","potential_consequence":"Oversubscription spikes CPU-seconds per image"},
 {"lens":"adversarial","id":"ADV-17","location":"owner 1j; config.py:184","trigger_condition":"PC and AWS both sync during fallback window","guard_snippet":"Shadow mode: PC sync off, compare outputs; runbook flips one system","potential_consequence":"Sheet values flip-flop between engines"},
 {"lens":"adversarial","id":"ADV-18","location":"project-context.md account default 1g","trigger_condition":"AWS Free plan auto-closes after 6 months or credits","guard_snippet":"Choose Paid plan at sign-up; record in PRD","potential_consequence":"Production stops at month 6"},
 {"lens":"verification-gap","id":"VG-01","location":"ocr.py:109-153; image_preprocess.py:428-471","trigger_condition":"No test runs preprocess/OCR across OS/package/model change","guard_snippet":"Golden parity test vs PC reference xlsx (real, local) + synthetic ground truth (CI)","potential_consequence":"Detector drift shrinks sessions; no test fails","gap_shape":"regression-gap","consumer":"main.extract_calls main.py:80-155","evidence":"tests.py has no reference to preprocess(, run_ocr, group_lines_into_rows"},
 {"lens":"verification-gap","id":"VG-02","location":"main.py:241-322, 325-376","trigger_condition":"Archive/retry/refuse semantics to be ported are unpinned","guard_snippet":"Characterisation tests for _process_one_folder/_run_batch with mocked OCR+sync","potential_consequence":"Port archives after failed sync; day never reaches Sheet","gap_shape":"regression-gap","consumer":"batch entry main.py:438-444","evidence":"tests.py has no reference to orchestration functions"},
 {"lens":"verification-gap","id":"VG-03","location":"excel_writer.py:37-122","trigger_condition":"xlsx values/formulas/expansion unverified across openpyxl/OS change","guard_snippet":"Write 17 sessions into sprs.xlsx; assert B/C/D, B2 range, B3, formats","potential_consequence":"Expanded day B2 omits rows; wrong totals","gap_shape":"regression-gap","consumer":"main.py:284; resync_output.py:73-94","evidence":"tests.py has no reference to write_sessions/excel_writer"},
 {"lens":"verification-gap","id":"VG-04","location":"config.py:50, 93","trigger_condition":"Env-var parsing of load-bearing flags unverified","guard_snippet":"Assert defaults; test 'false'/'0'/'' parse to False","potential_consequence":"Silent fallback or oneDNN re-enabled in AWS","gap_shape":"regression-gap","consumer":"ocr.py:395-440, 145","evidence":"tests.py has no reference to ocr_strict_engine/paddle_enable_mkldnn"},
 {"lens":"verification-gap","id":"VG-05","location":"config.py:23-28","trigger_condition":"Credential loader replaced; tests patch CONFIG directly","guard_snippet":"Loader tests: env overrides secrets_local; empty when neither; secret never logged","potential_consequence":"Wrong env name ships; every sync fails after deploy","gap_shape":"regression-gap","consumer":"sheets_sync.py:41-52","evidence":"tests.py:439-459 patches CONFIG; no config import test"},
 {"lens":"verification-gap","id":"VG-06","location":"tools/check_private_data.py:115-123","trigger_condition":"Value scan skipped and exit 0 when private files absent (CI)","guard_snippet":"Fail closed in CI without a denylist; test planted value exits 1","potential_consequence":"Real names/IDs pass CI and get published","gap_shape":"broken-verification-gap","consumer":".githooks/pre-commit, pre-push, planned CI","evidence":"no test references check_private_data; :115-117 only prints a note"},
 {"lens":"verification-gap","id":"VG-07","location":"resync_output.py:73-111","trigger_condition":"Backfill tool to be ported; filename/sheet logic untested and divergent","guard_snippet":"Tests for collect_files and read_sessions","potential_consequence":"Repair run silently skips days","gap_shape":"regression-gap","consumer":"docs/TECHNICAL.md:120-132 recovery runbook","evidence":"tests.py has no reference to resync_output/read_sessions/collect_files"}
]
```
