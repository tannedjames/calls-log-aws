---
stepsCompleted: [step-01-validate-prerequisites, step-02-design-epics, step-03-create-stories, step-04-final-validation]
status: complete
inputDocuments:
  - _bmad-output/planning-artifacts/prds/prd-calls-log-2026-10-14/prd.md
  - _bmad-output/planning-artifacts/prds/prd-calls-log-2026-10-14/addendum.md
  - _bmad-output/planning-artifacts/architecture/architecture-calls-log-2026-10-14/ARCHITECTURE-SPINE.md
  - _bmad-output/planning-artifacts/architecture/architecture-calls-log-2026-10-14/solution-design.md
  - _bmad-output/planning-artifacts/risk-assessment.md
  - _bmad-output/project-context.md
---

# calls-log - Epic Breakdown

## Overview

This document provides the complete epic and story breakdown for calls-log, decomposing the requirements from the PRD, UX Design if it exists, and Architecture requirements into implementable stories.

## Requirements Inventory

### Functional Requirements

FR1: The Operator can upload a Folder (≤ 40 images) from the office PC's browser in one action and mark it Ready. No long-lived AWS key exists on the PC, and the Folder Status is visible after upload.
FR2: Before any OCR, the System rejects a Folder whose name doesn't parse to a name and a valid calendar date, doesn't match a registered employee, has 0 or more than 40 supported images, or holds only unsupported formats (e.g. HEIC). It states the reason in plain words and uses zero OCR compute. (Accepted deviation: the PC still writes a Day Sheet for unknown names.)
FR3: Only Ready Folders are processed. An interrupted upload processes nothing, and a Ready Folder can't be changed: to correct a day, the Operator uploads the complete Folder again. An upload never alters a Run in progress.
FR4: For every Ready Folder, the System produces the same Sessions, Day Sheet contents and Sync payload as the production PC. The load-bearing settings (strict engine, oneDNN off, 4 exceptions per day) are unchanged and checked by a test.
FR5: The Google Sheet is updated within 2 h of Ready (p95 over the first 4 weeks after cutover). A typical Folder (< 20 images) finishes its Run in ≤ 10 min; a 40-image Folder in ≤ 20 min.
FR6: At most one Run per Person-Day executes at a time. No duplicate day tab is created by concurrent Syncs, and names that differ only in Unicode form are the same Person-Day.
FR7: The Owner can re-queue any stored Folder (including Parked) without re-uploading. Earlier Day Sheets are kept; the Sheet tab is overwritten as today.
FR8: The System Syncs with the existing Web App contract: identical payload; 3 attempts with 1 s and 2 s backoff and a 60 s timeout; `ok:false` or non-JSON counts as failure. No Web App code change; only the secret value rotates.
FR9: A Folder becomes Done only when its Day Sheet is stored and its Sync succeeded. Otherwise it becomes Failed and another Run is scheduled. Zero Sessions fails without storing a Day Sheet.
FR10: After 3 failed Runs a Folder is Parked: no more automatic Runs, no compute, and the Owner is alerted with the Folder and the last error.
FR11: The Owner can list Day Sheets by employee and date, and download any one in ≤ 1 min without the AWS console.
FR12: Screenshots are deleted 30 days after upload. Day Sheets are never deleted automatically and are versioned (earlier versions recoverable). Logs are kept 30 days.
FR13: The Owner can re-send stored Day Sheets for an employee and/or date to the Web App, with a dry run. It accepts every name and date form processing accepts.
FR14: Before cutover, all Day Sheets in the PC's `output/` are imported once. The count equals the PC's, and a sample is byte-identical.
FR15: The Operator and Owner see each Folder's Folder Status, last Run time and last error where the Operator uploads, without the AWS console. Changes show within 5 min.
FR16: The Owner gets an email when a Folder is Parked, when actual or forecast monthly cost passes 50%, 80% or 100% of $2, and when a Guardrail stops processing.
FR17: The AWS bill is ≤ $2.00 every month at 2,000 images a week, and the cost model also fits at 18 employees (~2,800 images a week).
FR18: No failure mode (retry loop, upload flood, stuck Run) can spend more than $0.50 before processing stops automatically and the Owner is alerted. Proven by a poison-Folder test and a flood test.
FR19: The Web App secret lives only in a managed secret store and is read at Run time; never in the image, repo or logs. It is rotated at cutover (Web App, AWS, PC). The PC keeps `secrets_local.py`.
FR20: Screenshots, Day Sheets and Folder Status records are encrypted at rest and in transit. Public access is impossible by configuration, and only the processing identity and the Owner can read them.
FR21: Every AWS identity is least-privilege. CI authenticates via GitHub OIDC limited to the repo's `main` branch. No long-lived access keys exist anywhere.
FR22: The CI privacy gate blocks publishing employee data or the secret, and fails closed when the private files are absent.
FR23: All AWS resources are in Terraform, and `terraform plan` on `main` shows no drift. A new account stands up from code plus a short documented list of manual steps.
FR24: A push to `main` runs the tests, the privacy gate and the Synthetic Set check, then builds the image, pushes to ECR and deploys, through OIDC only. A failed gate blocks the deploy.
FR25: The runtime is built from a hashed lockfile matching production's versions, with models baked in and their checksums recorded. No model is downloaded at Run time, and a commit always builds an identical dependency set.
FR26: A golden parity test on the Owner's PC compares the AWS runtime with the Reference Set. All three folders must be identical (folder 3 = the Owner-confirmed rule-based result). The Reference Set never leaves the PC. The test runs before cutover and after any dependency or model change.
FR27: Every change runs the Synthetic Set through the real OCR runtime against its ground truth. A mismatch blocks the deploy.
FR28: Comparison Period: for 2 weeks the PC and AWS process the same Folders, with Sync on wherever the Owner chooses. Cutover requires 100% Parity.
FR29: Independent Sync Switches on the PC and AWS (PC only, AWS only, both). Each is one documented change taking effect for the next Run, and both settings are visible in one place.
FR30: The Owner can move processing back to the PC (PC Sync on, AWS off) within 30 min, at any time, with no end date. This is rehearsed before cutover. FR26 is re-run after any dependency or model change on either side.
FR31: The repo holds a case-study pack (architecture diagram, ADRs, measured cost and turnaround report, runbook, Synthetic Set generator), free of private data.
FR32: OCR applies page-orientation classification only to images that are landscape after preprocessing. Folder 3 image 3 yields its 7 calls; all three references match; landscape photos are still classified. The change is in the shared core, so the PC and AWS behave identically.

### NonFunctional Requirements

NFR1: Availability — no uptime SLA; a failed Run is recoverable the same day; switch back to the PC in 30 min at any time; zero Day Sheets lost.
NFR2: Performance — see FR5; cold starts are acceptable within the 2 h target.
NFR3: Cost — the **total** AWS bill is at most **$2.00 per month** (hard cap; expected ≈ $0.40/month). To stay under it, only services billed by actual usage are allowed (per request, per second of compute, per GB stored); nothing with a fixed monthly fee (e.g. a NAT gateway, ≈ $30+/month even when idle) unless the cost model shows it fits.
NFR4: Security baseline — FR19–FR22; root account MFA with no root access keys; billing alerts on.
NFR5: Observability — every Run logs Folder, Folder Status, duration and outcome, and never OCR row text; failure and duration metrics feed alerting; logs kept 30 days.
NFR6: Reproducibility and maintainability — one-command deploy from `main`; the existing 81 tests keep passing; new tests cover the orchestration rules ported from `main.py`, the Day Sheet writer and the config invariants.
NFR7: Data governance — all data stays in `ap-southeast-1`; screenshots and logs 30 days, Day Sheets indefinitely; classification: PII (customer phone numbers) and employee working hours.
NFR8: The PC stays a full alternative writer with no end date, on the same pinned versions as AWS.

### Additional Requirements

From the architecture spine (Route B, Balanced: decision D-8) and solution design:

- **Brownfield, no starter template.** Epic 1 Story 1 is the Phase 0 parity and runtime spike, not a scaffold.
- **AD-1 Frozen core:** new code only in `cloud/` (adapters) and `tools/`. Core modules are imported, never forked. A test fails if the core imports `cloud`. `import runtime_env` stays first in every entrypoint.
- **AD-2 Run unit:** one Ready Folder per Run, in one process, images in sorted order, then `dedupe → build_sessions → write_sessions → sync_day` once. No per-image fan-out.
- **AD-3 Pinned runtime:** one hashed `requirements.lock` from the production `pip freeze`, used by the PC and the image. Python 3.13, x86_64 only. Models baked with SHA-256.
- **AD-4 State owner:** the DynamoDB `calls-log-folders` item is the only Folder Status. Only `cloud/status.py` writes it, with conditional transitions. No S3 moves or renames as state.
- **AD-5 Lease:** a conditional Person-Day lease (now + 1,000 s) before work starts; on a lease miss the message returns.
- **AD-6 Attempts:** counted at Run start, after taking the lease. Attempts > 3 → Parked + alert + message deleted. SQS DLQ (`maxReceiveCount` 10) is a crash backstop with an alarm.
- **AD-7 Commit order:** zero Sessions → Failed (no Day Sheet). Otherwise Day Sheet stored → Sync (if the AWS switch is on) → Done.
- **AD-8 Keys:** `inbox/<uploadId>/<file>`, `work/<uploadId>/<file>.json`, `results/<Person-Day>.xlsx` (versioned). `uploadId` = UTC timestamp + 8 hex. `cloud/naming.py` is the single parser and validator. The import makes current the file `resync_output.collect_files` would pick.
- **AD-9 Settings:** `cloud/settings.py` is the only reader of `CALLS_LOG_*` and SSM. Load-bearing flags are not overridable. SSM parameters are created with `ignore_changes` on value. `PADDLE_PDX_CACHE_HOME=/tmp/paddlex`, `PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True`.
- **AD-10 Switches:** AWS reads `/calls-log/sync_enabled` per Run; the PC keeps `CONFIG.sheets_sync_enabled`; resync mode ignores the AWS switch.
- **AD-11 Guardrails:**
  - runner: event-source `MaximumConcurrency` 2, timeout 900 s; SQS visibility timeout 1,200 s;
  - kill switch disables the event source mapping on an alarm (errors, DLQ depth, GB-s);
  - reserved concurrency (runner 3, intake 2) only after a quota increase;
  - $2 budget action is the last resort;
  - no NAT, ALB, VPC endpoints, RDS, EKS, KMS customer keys, Secrets Manager or Route 53;
  - a PR adding a resource states its monthly cost.
- **AD-12:** no VPC attachment.
- **AD-13 Terraform:** ≥ 1.10, `hashicorp/aws ~> 6.56`, one root module `infra/` for `prod`, S3 state with `use_lockfile`. Apply only from GitHub Actions on `main` via OIDC (`aws-actions/configure-aws-credentials@v6`). Bootstrap (state bucket, OIDC provider) applied by hand once.
- **AD-14 Logs:** one JSON line per Run transition; no OCR text, image content or secrets; 30-day log groups.
- **AD-15 Portable runner:** `handler(event, context)` and `python -m cloud.runner --upload-id`, both calling `run_folder(upload_id)`. The same image runs on Lambda and ECS.
- **AD-16 Checkpoints:** per-image `CallRecord` checkpoints in `work/`. A resumed Run OCRs only the missing images and merges in sorted order.
- **AD-17 Intake exposure:**
  - Function URL auth `NONE` with both `lambda:InvokeFunctionUrl` and `lambda:InvokeFunction` (`InvokedViaFunctionUrl`) statements;
  - constant-time passphrase check against the SSM hash;
  - presigned POST ≤ 15 MB per image, image types only, 15-min expiry;
  - CORS limited to the Function URL origin.
- **AD-18 Data baseline:** BPA, SSE-S3, deny non-TLS, Object Ownership enforced. `results/` versioned with no expiry; `inbox/` and `work/` expire after 30 days. DynamoDB default encryption. Region-locked.
- **Phase 0 exit criteria:**
  1. lockfile from the production freeze, or from this machine's set if the Owner standardises on it;
  2. Reference Set parity in the runner image on the PC (Docker or WSL2);
  3. Lambda memory sweep: projected ≤ 350k GB-s/month at 12,000 images;
  4. a 40-image Folder fits in one invocation or resumes;
  5. `mesa-libGL` + `glib2` via `dnf` + a `diagnose.py` smoke test in the image;
  6. Lambda concurrency quota raised;
  7. Function URL `GET /` returns 200.
- **Risk-driven tests and fixes:**
  - VG-02: orchestration characterisation tests.
  - VG-03: Day Sheet writer test.
  - VG-04: config invariants and env-parsing tests.
  - VG-05: credential-loader tests.
  - VG-06: privacy gate fails closed.
  - EC-07b / EC-08: resync uses the shared naming and the configured sheet.
  - ADV-16 / EC-12: thread counts from the vCPU allocation. Measured: the default 10 threads made real images 2–3× slower on the laptop.
- **Integration:** the Apps Script Web App is unchanged. Egress to `script.google.com` and `script.googleusercontent.com` without a VPC.
- **Data migration (Phase 7 "DB migration"):** FR14 import of `output/` → `results/` with the current-version rule, plus a Folder Status backfill as `Done (imported)`.
- **DNS/TLS (Phase 7):** no custom domain. AWS-managed HTTPS on the Function URL. Cutover = the Operator moves to the upload page + secret rotation + the Sync Switch setting.
- **Account setup:** AWS Paid plan (the Free plan closes after 6 months), root MFA, Service Quotas request for Lambda concurrency, alert email confirmation; SSM values set out-of-band.
- **Reference Set facts (D-9 to D-12):** 3 folders outside the repo; folder 3 includes one photo. This machine's package set reproduces all three once FR32 is applied.

### UX Design Requirements

No UX design document exists (single-operator internal tool; the PRD treated UX as out of scope). The upload page's behaviour is fully specified by FR1, FR2, FR3 and FR15, plus AD-17 (security). No separate UX-DR items.

### FR Coverage Map

FR1: Epic 4 - Browser upload of a Folder and marking it Ready
FR2: Epic 4 - Reject invalid Folders before OCR (validation in shared `cloud/naming.py`, built in Epic 3)
FR3: Epic 4 - Only Ready Folders processed; Ready Folders immutable
FR4: Epic 1 - Identical results to the production PC
FR5: Epic 3 - Turnaround ≤ 2 h; Run time budgets
FR6: Epic 3 - One Run per Person-Day (lease); Unicode-equivalent names
FR7: Epic 3 - Owner re-queues a stored or Parked Folder
FR8: Epic 3 - Same Web App contract
FR9: Epic 3 - Commit rule (Day Sheet stored + Sync ok → Done)
FR10: Epic 3 - 3 failed Runs → Parked + alert
FR11: Epic 4 - Find and download Day Sheets without the console
FR12: Epic 2 - Retention via lifecycle rules and versioning
FR13: Epic 3 - Re-send stored Day Sheets (resync mode, dry run)
FR14: Epic 5 - One-time import of PC `output/` (DB migration)
FR15: Epic 4 - Folder Status on the upload page
FR16: Epic 2 (cost alerts) + Epic 3 (Parked and Guardrail alerts)
FR17: Epic 2 - $2/month budget and cost model; measured in Epic 5
FR18: Epic 3 - Runaway protection ≤ $0.50 (kill switch, concurrency cap)
FR19: Epic 2 (secret store) + Epic 3 (read at Run time) + Epic 5 (rotation at cutover)
FR20: Epic 2 - Encryption and no public access (data baseline)
FR21: Epic 2 - Least privilege; GitHub OIDC; no long-lived keys
FR22: Epic 2 - Privacy gate fails closed in CI
FR23: Epic 2 - Terraform for everything, no drift
FR24: Epic 2 - CI/CD: tests, gates, build, push to ECR, deploy
FR25: Epic 1 - Hashed lockfile, baked models, no runtime downloads
FR26: Epic 1 - Golden parity test on the Owner's PC
FR27: Epic 1 (Synthetic Set + local check) + Epic 2 (CI gate)
FR28: Epic 5 - 2-week Comparison Period
FR29: Epic 3 (AWS Sync Switch) + Epic 5 (both switches documented)
FR30: Epic 5 - Switch back to the PC in 30 min; rehearsal
FR31: Epic 6 - Case-study pack
FR32: Epic 1 - Orientation classification only for landscape images

**Phase 7 checklist:** containerise → Epic 1 · move state out of local folders → Epic 3 · secrets → Epics 2, 3, 5 · IaC → Epic 2 · CI/CD with GitHub OIDC → ECR → Epic 2 · DB migration → Epic 5 · DNS/TLS cutover → Epic 5 · rollback → Epic 5.

## Epic List

### Epic 1: An OCR runtime you can pin and prove
The Owner gets a Linux container that produces the same results as the PC, proven against the Reference Set. The photo-orientation fix lands in the shared core, so the PC benefits immediately.
**FRs covered:** FR4, FR25, FR26, FR27 (local), FR32

### Epic 2: An AWS account you can change safely from GitHub, inside $2
The Owner can stand up and change the whole AWS environment from code. Deploys run from GitHub with no stored keys, cost alerts are on, and data is locked down by default.
**FRs covered:** FR12, FR16 (cost alerts), FR17, FR19 (store), FR20, FR21, FR22, FR23, FR24, FR27 (CI)

### Epic 3: Folders processed in AWS automatically
A queued Folder is processed end to end by the unchanged pipeline:
- the Day Sheet is stored;
- the Sheet is synced if the AWS switch is on;
- a failing Folder is parked after 3 tries, with an email;
- runaway spend stops itself.

The Owner can reprocess Folders and re-send results. In this epic the Owner drives it with a short command.
**FRs covered:** FR5, FR6, FR7, FR8, FR9, FR10, FR13, FR16 (Parked/Guardrail), FR18, FR19 (read), FR29 (AWS switch)

### Epic 4: Operator self-service upload and status page
The Operator uploads Folders in the browser, gets bad ones rejected up front, marks them Ready and watches their status. The Owner can download any Day Sheet. No AWS console or AWS keys are needed.
**FRs covered:** FR1, FR2, FR3, FR11, FR15

### Epic 5: Cutover with a safety net
History is imported, the PC and AWS are compared for 2 weeks, the Operator moves to the page and the secret is rotated, and switching back to the PC is rehearsed. A documented switch picks PC, AWS or both at any time.
**FRs covered:** FR14, FR19 (rotation), FR28, FR29 (both switches), FR30

### Epic 6: The case-study pack
The Owner has employer-ready material: architecture diagram, decision records, measured cost and turnaround, and the runbook. All of it is free of private data.
**FRs covered:** FR31

**Story sizes:** S ≈ one day or less · M ≈ a few days · L ≈ a week or more (single developer).

## Epic 1: An OCR runtime you can pin and prove

The Owner gets a Linux container that produces the same results as the PC, proven against the Reference Set. The photo-orientation fix lands in the shared core, so the PC benefits immediately.

### Story 1.1: Pin the OCR runtime to one hashed lockfile

As the Owner,
I want the PC and the container to install from the same hashed lockfile,
So that every install has the same OCR packages and therefore the same results.

**Size:** S · **Covers:** FR25, NFR8, AD-3

**Acceptance Criteria:**

**Given** the chosen freeze (the production PC's `pip freeze`, or this machine's 2026-10-14 set if the Owner standardises on it)
**When** `requirements.lock` is generated with hashes
**Then** `pip install --require-hashes -r requirements.lock` in a clean venv reproduces exactly those versions
**And** `opencv-python` and `opencv-contrib-python` are both 4.10.0.84

**Given** the locked venv
**When** `run_tests.py` runs
**Then** all 81 existing tests pass

**Given** the repo documentation
**When** someone follows the install steps
**Then** README and TECHNICAL say to install from `requirements.lock`, and `requirements.txt` is kept only for the version ranges

### Story 1.2: Stop re-rotating upright photos

As the Owner,
I want page-orientation classification to run only on landscape images,
So that upright phone photos are read instead of producing no calls.

**Size:** S · **Covers:** FR32, FR4, AD-1

**Acceptance Criteria:**

**Given** an image that is portrait after preprocessing
**When** OCR runs
**Then** page-orientation classification is skipped for that image
**And** for a landscape image it still runs

**Given** Reference Set folder 3
**When** it is processed on the PC
**Then** the result is the 6 rule-based Sessions (08:01–09:57, 11:22–12:01, 12:36–13:48, 13:54–14:17, 14:23–14:23, 14:30–15:01)
**And** folders 1 and 2 are unchanged (same Sessions; 101 and 102 call rows)

**Given** the unit tests
**When** they run without OCR models
**Then** a test proves the portrait/landscape decision using a stubbed engine
**And** `docs/TECHNICAL.md` describes the behaviour

### Story 1.3: Tests that pin the behaviour the migration must preserve

As the Owner,
I want the folder-processing rules, the Excel output and the load-bearing settings covered by tests,
So that porting to AWS cannot silently change behaviour.

**Size:** M · **Covers:** FR4, FR9, NFR6 (risk VG-02, VG-03, VG-04)

**Acceptance Criteria:**

**Given** `_process_one_folder` with `extract_calls` and `sync_day` stubbed and temporary folders
**When** sync fails
**Then** the xlsx exists and the source folder stays in place
**And** when zero Sessions result it raises and writes nothing, and on success the folder is archived

**Given** 17 Sessions
**When** they are written into `sprs.xlsx`
**Then** columns B and C hold numeric day-fractions, D holds `=C-B` formulas, B2 sums the expanded range, and B3 is `=B7`
**And** the number formats are kept

**Given** the default `CONFIG`
**When** the invariant test runs
**Then** `ocr_strict_engine is True`, `paddle_enable_mkldnn is False` and `post_9am_exception_limit == 4`
**And** all new tests run under `run_tests.py` without OCR models in under 60 s

### Story 1.4: A made-up screenshot set with known answers

As the Owner,
I want a generator of fictional call-log screenshots with ground truth,
So that OCR can be regression-tested anywhere, including CI, without real data.

**Size:** S · **Covers:** FR27 (local), FR31

**Acceptance Criteria:**

**Given** `tools/gen_synth.py --seed N`
**When** it runs
**Then** it writes screenshots, photo variants and a ground-truth JSON, identical for the same seed

**Given** the current runtime
**When** `tools/check_synth.py` compares parsed calls with the ground truth
**Then** every call matches, for both screenshots and photos

**Given** the generated files
**When** the privacy check runs
**Then** it passes, and the files contain no real names or phone numbers

### Story 1.5: Linux runner image (containerise)

As the Owner,
I want the pipeline packaged as an x86_64 Linux image on the Lambda Python 3.13 base, with models baked in,
So that it runs identically anywhere without downloading anything.

**Size:** M · **Covers:** FR25, AD-3, AD-9, AD-15 (risk ADV-07, ADV-11, ADV-16)

**Acceptance Criteria:**

**Given** the Dockerfile
**When** it is built
**Then** it installs from `requirements.lock`, adds `mesa-libGL` and `glib2` via `dnf` (or the documented headless-OpenCV fallback), and copies the OCR models after checking their SHA-256

**Given** the image with networking disabled
**When** `diagnose.py` runs on a Synthetic Set screenshot inside it
**Then** it prints usable call rows
**And** no model download is attempted (`PADDLE_PDX_CACHE_HOME`, `PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK` set)

**Given** a container limited to 2 vCPUs
**When** OCR runs
**Then** the OMP/MKL threads and `paddle_cpu_threads` come from the CPUs actually available, not the host's count

**Given** the built image
**When** the build check lists its files
**Then** it contains no `secrets_local.py`, no `Employees.gs`, no `input_images/` and no `output/`
**And** the compressed image size is recorded

### Story 1.6: Parity check on the Owner's PC

As the Owner,
I want one command that runs the runner image on my Reference Set and compares the Sessions with the expected ones,
So that I can prove identical output without the data leaving my PC.

**Size:** M · **Covers:** FR26, FR4

**Acceptance Criteria:**

**Given** the reference folders and an expectations file, both outside the repo
**When** `tools/parity_check.py --image <tag> --ref <dir>` runs (Docker Desktop or WSL2)
**Then** it reports IDENTICAL or DIFFERENT per folder, with the first differing Session

**Given** today's three reference folders and Story 1.2
**When** the check runs
**Then** all three are IDENTICAL

**Given** any run
**When** it finishes
**Then** nothing was written into the reference folders, folders are labelled F1…Fn (no names or phone numbers in the output), and the exit code is non-zero on any difference
**And** the docs explain the Docker Desktop or WSL2 setup

## Epic 2: An AWS account you can change safely from GitHub, inside $2

The Owner can stand up and change the whole AWS environment from code. Deploys run from GitHub with no stored keys, cost alerts are on, and data is locked down by default.

### Story 2.1: Account baseline, private repo and Terraform bootstrap

As the Owner,
I want a secured AWS account, the code in a private GitHub repo, and the Terraform state and GitHub trust set up once,
So that everything after this can be deployed from code without long-lived keys.

**Size:** S · **Covers:** FR21, FR23, NFR4, AD-13 (risk ADV-15, ADV-18)

**Acceptance Criteria:**

**Given** a new AWS account on the **Paid plan**
**When** the baseline checklist is done
**Then** the root user has MFA and no access keys, the Owner works through a non-root identity, and a Lambda concurrency quota increase has been requested (`aws lambda get-account-settings` shows the result)

**Given** the repo
**When** it is pushed to a private GitHub repository
**Then** `tools/check_private_data.py` printed OK before the first push, and the hooks are enabled

**Given** `infra/bootstrap/` (applied by hand once)
**When** it is applied
**Then** a state bucket exists with versioning, Block Public Access, SSE and a deny-non-TLS policy
**And** a GitHub OIDC provider and deploy role exist, trusting only `repo:<owner>/<repo>:ref:refs/heads/main` with `aud = sts.amazonaws.com`

**Given** the documentation
**When** a new account is set up from it
**Then** the manual steps are listed in one place: Paid plan, MFA, quota request, bootstrap apply, budget email confirmation

### Story 2.2: CI that tests, fails closed on private data, and checks OCR

As the Owner,
I want every push and PR to run the tests, a privacy gate that cannot silently pass, and the Synthetic Set OCR check,
So that broken or private changes never reach `main`.

**Size:** M · **Covers:** FR22, FR24, FR27 (CI), NFR6 (risk VG-06)

**Acceptance Criteria:**

**Given** a push or PR
**When** `ci.yml` runs
**Then** `run_tests.py` runs, `check_private_data.py` runs in fail-closed mode, and the runner image is built and runs the Synthetic Set check

**Given** CI without `secrets_local.py` or `Employees.gs`
**When** no private denylist secret is configured
**Then** the privacy gate fails (it no longer prints a note and passes)
**And** with a hashed denylist supplied from a GitHub secret, a planted fake value in a tracked file makes it exit 1, verified by a test

**Given** a PR targeting `main` with any failing CI job
**When** someone tries to merge it
**Then** branch protection blocks the merge (CI is a required check)

### Story 2.3: Deploy pipeline: GitHub OIDC → ECR → Terraform apply

As the Owner,
I want merges to `main` to build, push and deploy automatically through OIDC,
So that I never handle AWS keys and every change is reproducible.

**Size:** M · **Covers:** FR21, FR23, FR24, AD-13

**Acceptance Criteria:**

**Given** the `infra/` root module (Terraform ≥ 1.10, `hashicorp/aws ~> 6.56`, S3 backend with `use_lockfile`) whose first resource is the ECR repository
**When** a merge to `main` with CI green runs `deploy.yml`
**Then** it authenticates with `aws-actions/configure-aws-credentials@v6` (OIDC), runs `terraform apply`, and pushes the runner image to ECR tagged with the git SHA

**Given** a pull request
**When** the workflow runs
**Then** `terraform plan` output is posted, and nothing is applied

**Given** a workflow from another branch or a fork
**When** it tries to assume the deploy role
**Then** STS refuses it

**Given** the ECR repository
**When** images accumulate
**Then** tags are immutable and a lifecycle rule keeps only the last 2 images

**Given** the GitHub settings
**When** they are inspected
**Then** no AWS access key exists in secrets; only the role ARN is stored, as a variable

### Story 2.4: Data stores, secret parameters and the $2 budget

As the Owner,
I want the storage, secret parameters and cost alerts added to Terraform and deployed through the pipeline,
So that data is protected by default and I'm warned long before $2.

**Size:** M · **Covers:** FR12, FR16 (cost), FR17, FR19 (store), FR20, NFR3, NFR7, AD-9, AD-11, AD-18

**Acceptance Criteria:**

**Given** the data bucket added to `infra/`
**When** the pipeline applies it
**Then** it has Block Public Access, SSE-S3, deny-non-TLS, Object Ownership enforced and versioning, in `ap-southeast-1`
**And** `inbox/` and `work/` expire after 30 days while `results/` never expires

**Given** the bucket
**When** a public-read or non-TLS request is attempted
**Then** it is denied

**Given** the SSM SecureString parameters `/calls-log/{webapp_url, sync_secret, sync_enabled, name_prefixes, intake_passphrase_hash, alert_email}`
**When** the pipeline applies them
**Then** they exist with `ignore_changes` on value, and `sync_enabled` defaults to `false`

**Given** the real secret values set out-of-band
**When** the Terraform state is inspected
**Then** it contains only placeholders, never the real values

**Given** the $2 monthly budget and an SNS topic with the Owner's confirmed email
**When** actual or forecast cost passes 50%, 80% or 100%
**Then** the Owner gets an email

## Epic 3: Folders processed in AWS automatically

A queued Folder is processed end to end by the unchanged pipeline:
- the Day Sheet is stored;
- the Sheet is synced if the AWS switch is on;
- a failing Folder is parked after 3 tries, with an email;
- runaway spend stops itself.

The Owner can reprocess Folders and re-send results. In this epic the Owner drives it with a short command.

### Story 3.1: Measure Lambda cost per image and choose the memory size

As the Owner,
I want the runner image's real cost per image measured in Lambda before the rest is built,
So that the $2 plan rests on data and the Fargate fallback is chosen only if needed.

**Size:** S · **Covers:** FR5, FR17, NFR3 (Phase 0 exit)

**Acceptance Criteria:**

**Given** the runner image in ECR and a temporary benchmark Lambda deployed through the pipeline behind an `enable_bench` Terraform variable
**When** the Synthetic Set runs at 1,536, 2,048 and 3,008 MB
**Then** GB-s per image, wall time per image and cold-start time are recorded for each size

**Given** the measurements
**When** the memory size with the lowest GB-s per image is chosen
**Then** projected usage at 12,000 images a month is ≤ 350,000 GB-s
**And** if it isn't, an ADR records the switch to the Fargate Spot runner (AD-15) before Story 3.5

**Given** a 40-image Synthetic folder
**When** it runs at the chosen size
**Then** it finishes within 900 s, or the report records that checkpoints (AD-16) are needed for it

**Given** the spike
**When** it is complete
**Then** `enable_bench` is set back to false, the pipeline removes the benchmark resources, and the results are recorded in `docs/aws/`

### Story 3.2: Settings and secrets read at run time

As the Owner,
I want all cloud settings and the Web App secret read from the environment and SSM at run time,
So that no secret is ever in the image, and the PC keeps working unchanged.

**Size:** S · **Covers:** FR19 (read), FR29 (AWS switch), AD-9, AD-10 (risk VG-05, VG-04)

**Acceptance Criteria:**

**Given** `cloud/settings.py`
**When** a container starts
**Then** it reads `CALLS_LOG_*` variables and SSM parameters once and caches them for that container

**Given** the AWS Sync Switch
**When** each Run starts
**Then** `/calls-log/sync_enabled` is read fresh for that Run

**Given** `config.py` with the new env path
**When** both env values and `secrets_local.py` exist
**Then** the env values win
**And** when neither exists the values are empty and sync raises its existing error, proven by tests

**Given** an env value that tries to override `ocr_strict_engine`, `paddle_enable_mkldnn` or `post_9am_exception_limit`
**When** settings load
**Then** it is ignored, proven by a test
**And** a test shows the secret never appears in log output

### Story 3.3: One name parser for everything

As the Owner,
I want a single module that parses, validates and normalises folder names,
So that upload, processing, resync and import can never disagree about whose day a Folder is.

**Size:** S · **Covers:** FR6, FR2, FR13, AD-8 (risk EC-02, EC-07b, EC-08, EC-14)

**Acceptance Criteria:**

**Given** `cloud/naming.py`
**When** it parses `Nguyen Van A & 2026-06-01` (and the forms `main.parse_name_date` accepts)
**Then** it returns the NFC Person-Day key, the name and the date
**And** an impossible calendar date such as `2026-02-30` is rejected

**Given** names that differ only in Unicode form (NFC vs NFD)
**When** they are parsed
**Then** they give the same Person-Day key

**Given** the allowlist from `/calls-log/name_prefixes`
**When** a name is matched
**Then** matching uses the same fold as `norm_()` in `WebAppSync.gs`, proven against the same test strings

**Given** `resync_output.py`
**When** it collects files
**Then** it uses the shared parser and reads the configured sheet rather than the active one
**And** existing resync behaviour for correctly named files is unchanged, proven by a test

### Story 3.4: Folder Status store with lease and attempt counting

As the Owner,
I want each Folder's lifecycle recorded in one place with safe transitions,
So that a Folder is never processed twice at once, never lost half-way, and never retried forever.

**Size:** M · **Covers:** FR6, FR9, FR10, AD-4, AD-5, AD-6, AD-18

**Acceptance Criteria:**

**Given** the `calls-log-folders` DynamoDB table added to `infra/` in this story
**When** the pipeline applies it
**Then** it exists with on-demand capacity and default encryption, in `ap-southeast-1`

**Given** `cloud/status.py`
**When** transitions are applied
**Then** only the transitions in the AD-4 state diagram succeed, each as a conditional write from the expected prior state
**And** any other transition fails without changing the item

**Given** a Person-Day lease held by one Run
**When** a second Run tries to start for the same Person-Day
**Then** it cannot take the lease and its attempt count does not change

**Given** a Folder whose lease is taken
**When** a Run starts
**Then** the attempt count goes up before processing
**And** at the 4th start the Folder becomes Parked instead of running

**Given** the tests
**When** they run
**Then** DynamoDB is faked with `unittest.mock`, and a test proves no core module imports `cloud` (AD-1)

### Story 3.5: The runner processes one Folder end to end

As the Owner,
I want one Run to take a Ready Folder through the unchanged pipeline and store and sync its Day Sheet,
So that AWS produces exactly what the PC would.

**Size:** M · **Covers:** FR4, FR5, FR8, FR9, NFR2, NFR5, AD-2, AD-7, AD-14, AD-15

**Acceptance Criteria:**

**Given** a Pending Folder in `inbox/<uploadId>/`
**When** `run_folder(upload_id)` runs through `python -m cloud.runner --upload-id` in the runner container, or through `handler()` with a sample SQS event in a test
**Then** the images are processed in sorted order with the existing `extract_calls`, `dedupe`, `build_sessions` and `write_sessions`
**And** the Day Sheet is stored as `results/<Person-Day>.xlsx` (a new version)

**Given** the AWS Sync Switch is on
**When** the Day Sheet is stored
**Then** `sync_day` is called unchanged and the Folder becomes Done only after it succeeds
**And** with the switch off the Folder becomes Done without syncing

**Given** a Folder that yields zero Sessions
**When** the Run ends
**Then** the Folder is Failed and no Day Sheet is stored

**Given** the Synthetic Set and the Reference Set (via the CLI entrypoint on the Owner's PC)
**When** processed by the runner
**Then** the Sessions are identical to the local pipeline's

**Given** any Run
**When** it logs
**Then** it writes one JSON line per transition, with no OCR text, image content or secrets

### Story 3.6: Checkpoint and resume large Folders

As the Owner,
I want a Run that gets cut off part-way to resume where it stopped,
So that a 40-image Folder never fails forever at Lambda's 15-minute limit.

**Size:** S · **Covers:** FR5, FR4, AD-16

**Acceptance Criteria:**

**Given** a Run in progress
**When** each image finishes
**Then** that image's parsed call records are written to `work/<uploadId>/<file>.json`

**Given** a Run stopped part-way (simulated timeout)
**When** it is started again for the same upload
**Then** only the images without a checkpoint are OCR'd
**And** the records are merged in sorted image order, so the Sessions equal those of an uninterrupted Run (proven on the Synthetic Set)

**Given** checkpoints left behind
**When** 30 days pass
**Then** the `work/` lifecycle rule removes them

### Story 3.7: Queue, trigger and cost guardrails

As the Owner,
I want queued Folders processed automatically, at most two at a time, with an automatic stop if anything runs away,
So that the bill can't pass the cap before I hear about it.

**Size:** M · **Covers:** FR5, FR10, FR16 (Parked, Guardrail), FR18, FR21, NFR3, NFR5, AD-11, AD-12

**Acceptance Criteria:**

**Given** `infra/`
**When** it is applied
**Then** it creates:
- an SQS standard queue with visibility timeout 1,200 s and a DLQ with `maxReceiveCount` 10;
- the runner Lambda from the ECR image (timeout 900 s, memory from Story 3.1, no VPC);
- an event source mapping with `MaximumConcurrency` 2;
- a least-privilege runner role

**Given** `tools/enqueue.py` run by the Owner with short-lived credentials
**When** a folder is enqueued
**Then** its images are uploaded, its Folder Status becomes Pending, and a message is sent

**Given** a poison Folder that always fails
**When** it is enqueued
**Then** it is Parked after 3 Runs, no further invocations occur, and the Owner gets an email naming the Folder and the reason

**Given** 50 Folders enqueued at once
**When** they are processed
**Then** no more than 2 Runs execute concurrently

**Given** the guardrail alarm (errors, DLQ depth or GB-s over 15 min)
**When** it fires
**Then** the kill-switch Lambda disables the event source mapping within 5 min and emails the Owner
**And** re-enabling is a documented runbook step

**Given** the guardrails in place
**When** the worst case is calculated
**Then** the runaway spend before the stop is ≤ $0.50 and documented
**And** reserved concurrency is applied only when the account quota allows

### Story 3.8: Reprocess Folders and re-send stored results

As the Owner,
I want to re-queue any stored Folder and re-send stored Day Sheets to the Web App,
So that I can recover from fixes or outages without the PC.

**Size:** S · **Covers:** FR7, FR13, AD-10

**Acceptance Criteria:**

**Given** a Done, Failed or Parked Folder
**When** the Owner re-queues it
**Then** it becomes Pending with its attempt count reset, earlier Day Sheet versions stay recoverable, and the Sheet tab is overwritten as on the PC

**Given** runner `resync` mode with an employee and/or date filter
**When** it runs with `--dry-run`
**Then** it lists what it would send and sends nothing
**And** without `--dry-run` it sends the current `results/` versions, regardless of the AWS Sync Switch (as `resync_output.py` does)

## Epic 4: Operator self-service upload and status page

The Operator uploads Folders in the browser, gets bad ones rejected up front, marks them Ready and watches their status. The Owner can download any Day Sheet. No AWS console or AWS keys are needed.

### Story 4.1: A protected intake endpoint

As the Owner,
I want a public HTTPS endpoint that only people with the passphrase can use,
So that the Operator can work from a browser without AWS keys, and strangers can't use it.

**Size:** S · **Covers:** FR1, FR21, AD-17

**Acceptance Criteria:**

**Given** the intake Lambda Function URL (auth `NONE`)
**When** Terraform applies it
**Then** both resource-policy statements exist (`lambda:InvokeFunctionUrl`, and `lambda:InvokeFunction` conditioned on `lambda:InvokedViaFunctionUrl`)
**And** `GET /` returns 200 with the page

**Given** an API call without the correct passphrase
**When** it arrives
**Then** it gets 401 after a constant-time comparison against the SSM hash, without touching S3, DynamoDB or SQS

**Given** the bucket CORS rules
**When** they are inspected
**Then** only the Function URL origin is allowed

### Story 4.2: Validate and upload a Folder from the browser

As the Operator,
I want to pick a folder in the browser and have mistakes caught before anything uploads,
So that bad folders never reach processing.

**Size:** M · **Covers:** FR1, FR2

**Acceptance Criteria:**

**Given** a folder named `<Name> & yyyy-mm-dd` with 1–40 supported images
**When** the Operator selects it
**Then** the page and the server (via `cloud/naming.py`) both validate it, the server issues presigned POSTs under `inbox/<uploadId>/` (≤ 15 MB, image types only, 15-min expiry), the images upload in one action, and the Folder Status is Uploading

**Given** a folder with an unknown name, an impossible date, 41 images or only HEIC files
**When** the Operator selects it
**Then** it is rejected with the reason in plain words, no SQS message is sent, and no OCR compute is used

**Given** any upload
**When** it completes or fails
**Then** no AWS credential has been stored on the PC

### Story 4.3: Mark a Folder Ready and start processing

As the Operator,
I want to mark a fully uploaded Folder as Ready,
So that only complete days are processed.

**Size:** S · **Covers:** FR3

**Acceptance Criteria:**

**Given** a Folder in Uploading whose object count equals the declared count
**When** the Operator clicks Ready
**Then** the Folder Status becomes Pending and a queue message is sent

**Given** a Folder whose upload was interrupted
**When** the Operator clicks Ready
**Then** Ready is refused, nothing is processed, and the leftover objects expire by lifecycle

**Given** a Folder that is already Ready
**When** another upload is attempted into it
**Then** it is refused
**And** to correct a day the Operator uploads the complete Folder again, which creates a new upload and re-processes that Person-Day

### Story 4.4: Folder Status and Day Sheet download on the page

As the Operator and Owner,
I want to see each Folder's status and download any Day Sheet from the page,
So that neither of us needs the AWS console.

**Size:** S · **Covers:** FR11, FR15

**Acceptance Criteria:**

**Given** recent uploads
**When** the status view loads
**Then** it shows, per Person-Day, the latest Folder's Status, last Run time (local time, stored as UTC) and last error
**And** a status change appears within 5 min

**Given** an employee and a date
**When** the Owner searches
**Then** the matching Day Sheet downloads through a short-lived presigned link in ≤ 1 min

**Given** the page
**When** it is used
**Then** the current AWS Sync Switch setting is shown, read-only

## Epic 5: Cutover with a safety net

History is imported, the PC and AWS are compared for 2 weeks, the Operator moves to the page and the secret is rotated, and switching back to the PC is rehearsed. A documented switch picks PC, AWS or both at any time.

### Story 5.1: Import the PC's past results (the "DB migration")

As the Owner,
I want all existing Day Sheets from the PC's `output/` imported into AWS,
So that history and resync work from AWS from day one.

**Size:** S · **Covers:** FR14, AD-8

**Acceptance Criteria:**

**Given** the PC's `output/` folder
**When** `tools/import_outputs.py` runs (with `--dry-run` available)
**Then** every `<Name> & <date>[(n)].xlsx` is uploaded as a version of `results/<Person-Day>.xlsx`, ordered so that the current version is the file `resync_output.collect_files` would select
**And** each Person-Day gets a Folder Status item `Done (imported)`

**Given** the import finishes
**When** it is checked
**Then** the imported Person-Day count equals the PC's, a random sample of 10 matches byte-for-byte by checksum, and re-running the import changes nothing

### Story 5.2: Two-week Comparison Period

As the Owner,
I want the PC and AWS to process the same days for two weeks while their results are compared,
So that I only cut over on proven parity.

**Size:** M · **Covers:** FR28, FR29, SM-1, SM-5

**Acceptance Criteria:**

**Given** the same Folders processed on the PC and AWS
**When** `tools/compare_outputs.py` compares the PC's `output/` with `results/` each day
**Then** it reports per Person-Day IDENTICAL or the first differing Session, using F-labels in shared output

**Given** the Comparison Period
**When** it runs
**Then** Sync is on wherever the Owner chooses, and the setting is recorded daily in the runbook log
**And** the Operator's weekly hands-on time is recorded as the SM-5 baseline

**Given** the end of the 2 weeks
**When** cutover is considered
**Then** it proceeds only with 100% IDENTICAL
**And** any difference stops the plan until it is explained and fixed

### Story 5.3: Writer switches and switch-back rehearsal (rollback)

As the Owner,
I want a documented, rehearsed way to choose who writes to the Sheets and to move back to the PC,
So that I can recover from any AWS problem within 30 minutes.

**Size:** S · **Covers:** FR29, FR30, NFR1, NFR8

**Acceptance Criteria:**

**Given** the runbook
**When** the Owner follows it
**Then** the AWS switch (`/calls-log/sync_enabled`) and the PC switch (`config.py`) are each one documented change
**And** a table shows both current settings in one place

**Given** a timed rehearsal before cutover
**When** the Owner moves processing back to the PC (PC Sync on, AWS off) and processes one Folder there
**Then** it is done within 30 min, and the time is recorded

**Given** a dependency or model change on either side
**When** the runbook is followed
**Then** it requires Story 1.6's parity check to pass before both systems are trusted to write again

### Story 5.4: Cutover: rotate the secret and move the Operator to the page

As the Owner,
I want the Web App secret rotated and the Operator switched to the upload page,
So that AWS becomes the everyday path with a fresh secret, and no DNS or TLS work is needed.

**Size:** S · **Covers:** FR19 (rotation), FR28, FR1 (risk ADV-09)

**Acceptance Criteria:**

**Given** a new secret
**When** it is set in the live Web App (redeployed as a **new version of the existing deployment**, never a new deployment), in SSM `/calls-log/sync_secret` and in the PC's `secrets_local.py`
**Then** a sync with the old secret returns `bad secret`, and syncs from both the PC and AWS succeed with the new one

**Given** the Operator
**When** cutover day arrives
**Then** they have the page URL (AWS-managed HTTPS; no custom domain) and a one-page guide, and they upload that day's Folders through it

**Given** the first week after cutover
**When** it is monitored
**Then** p95 Ready-to-Sync time, alerts and cost are reviewed daily and logged

## Epic 6: The case-study pack

The Owner has employer-ready material: architecture diagram, decision records, measured cost and turnaround, and the runbook. All of it is free of private data.

### Story 6.1: Architecture and decisions write-up

As the Owner,
I want a sanitised write-up of the architecture and the decisions behind it,
So that I can show and defend the design in interviews.

**Size:** S · **Covers:** FR31

**Acceptance Criteria:**

**Given** the solution design, spine and decision log
**When** `docs/aws/` is written
**Then** it contains:
- the Route B architecture diagram;
- ADRs for the key decisions (route choice, no VPC, guardrails, single-flight lease, checkpoints, orientation fix);
- the runbook

**Given** the pack
**When** the privacy check runs
**Then** it passes, and the pack contains no employee names, spreadsheet IDs, URLs, secrets or phone numbers

### Story 6.2: Measured cost and turnaround report

As the Owner,
I want real numbers from the first full month,
So that the case study shows results, not estimates.

**Size:** S · **Covers:** FR31, FR17, SM-1 to SM-6

**Acceptance Criteria:**

**Given** the first full month after cutover
**When** the report is produced
**Then** it shows:
- the AWS bill by service;
- Lambda GB-s used against the 400,000 free;
- p95 Ready-to-Sync time from the logs;
- parity results;
- each success metric (SM-1 to SM-6) met or not

**Given** the planning estimates ($0.39/month, ~11 s per image)
**When** they are compared with the measured values
**Then** the report states the variance and why
