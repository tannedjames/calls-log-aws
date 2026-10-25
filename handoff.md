# Handoff: calls-log on AWS

Labels: [verified] = checked on 2026-10-25 for this file; [from session] = done or decided earlier, not re-checked; [unverified] = believed, not checked. Not the same file as `../HANDOFF.md` (outside the repo, holds real IDs; never copy it in).

## 1. Goal
calls-log reads employees' phone call-log screenshots with OCR, groups the calls into daily sessions, writes one Excel "Day Sheet" per employee-day (`<Name> & <yyyy-mm-dd>.xlsx`), and pushes the same day into that employee's Google Sheet through an Apps Script Web App. A small group of real employees and their manager (the Owner) depend on the Sheets daily [from session]. The work moves the run from the Owner's Windows PC to a serverless AWS pipeline (upload page → queue → Lambda runner → S3/DynamoDB → Sheets) that doubles as a cloud/database portfolio project, without breaking the live flow. Recent work added Owner controls on the upload page, a "second upload waits for Re-run" rule, a 4-hour runaway window, and removed AI attribution from the repo history.

## 2. Architecture
**Current production pipeline** (same code on the PC and in AWS) [verified: files and functions exist]:
- Input: one folder per employee-day `input_images/<Name> & <yyyy-mm-dd>/` of JPG/PNG screenshots or photos; `main.py` `gather_jpg_files` (l.57), batch `_run_batch` (l.325).
- Preprocess: `image_preprocess.py` `preprocess` (l.429): orientation (landscape only), contrast.
- OCR: `ocr.py` `_build_paddle_engine` (l.109); PaddleOCR, `PP-OCRv5_mobile_rec`, languages `["japan","en","vi"]` (`config.py` l.52-54, 77).
- Parse: `parser.py` `parse_row` (l.470), `parse_start_time_ex` (l.100), `parse_duration` (l.255).
- Validate/group: `session_builder.py` `dedupe` (l.78), `build_sessions` (l.139); rules in `docs/TECHNICAL.md` §5; confidence flag `ocr_confidence_threshold = 0.85`; `review_report.py`.
- Excel: `excel_writer.py` `write_sessions` (l.37) into template `sprs.xlsx`. Google: `sheets_sync.py` `sync_day` (l.32).
- AWS wrapper: `cloud/runner.py` `run_folder` (l.126) runs the same stages per image with checkpoints (`extract`, l.95).

**Target AWS** (region ap-southeast-2, Terraform in `infra/`, 44 resources) [verified: in code]; deployed and green on `ba6d3f3` [verified: Deploy run 40218854228].
- Storage, built: S3 `calls-log-data-<account>`: `inbox/<uploadId>/` (30 days), `work/` checkpoints (30 days), `results/<Person-Day>.xlsx` (versioned) (`infra/data.tf`).
- Compute, built: Lambda container images from ECR `calls-log-runner`: `calls-log-runner` (900 s, 2,048 MB, at most 2 at a time), `calls-log-intake` (upload page behind a public Function URL, passphrase-guarded), `calls-log-killswitch`, `bench` (benchmark, still enabled).
- Queue, built: SQS `calls-log-runs` (visibility 1,200 s) plus a DLQ after 10 receives (`infra/runner.tf`).
- Database, built: DynamoDB `calls-log-folders` (status only, see below) (`infra/status.tf`).
- Config, built: SSM `/calls-log/*`, 7 parameters (`infra/parameters.tf`).
- Guardrails, built: 5 CloudWatch alarms → kill switch plus SNS `calls-log-alerts`; $2 budget (`infra/guardrails.tf`, `infra/alerts.tf`).
- Scheduling, built: no cron. Event-driven: Ready on the page → SQS → runner. The Owner tab can stop and start the queue trigger.
- Delivery, built: Google Sheets through the Web App, gated by the AWS Sync Switch `/calls-log/sync_enabled`. Day Sheets download from the page.
- Planned: a relational store of call/session rows for reporting. Not designed yet [unverified need].

**Database** [verified: `cloud/status.py`]. One DynamoDB table, `calls-log-folders`:
- Keys: `pk = pd#<Person-Day>`, `sk = up#<uploadId>`, with one `sk = lease` item per day. Index `uploads-by-day` on (`upDay`, `sk`).
- Columns: `status`, which is one of Uploading, Pending, Processing, Done, Failed, Parked, Held, Superseded. Also `imageCount`, `attempts`, `readyAt`, `lastRunAt`, `lastError`, `createdAt`, `updatedAt`.
- It tracks uploads, not call rows. The Excel data lives only in the xlsx in S3 `results/`: rows 7-22 are sessions, col B = start, C = end, D = span (`[h]:mm`), B2 = total span (`config.py` l.168-179).

## 3. Run & test
- **Setup (PC, Windows):** `python -m venv .venv`, then `.venv\Scripts\pip install -r requirements.lock` (`docs/TECHNICAL.md` §2). Private files, both gitignored: `secrets_local.py` (copy `secrets_local.example.py`) and `apps_script/Employees.gs`.
- **Tests:** `.venv\Scripts\python.exe -X utf8 run_tests.py`. pytest isn't installed, and `-X utf8` is required.
- **Privacy gate:** `python -X utf8 tools/check_private_data.py` must print OK.
- **Sample data, no sync, no moves:**
  - `python tools/gen_synth.py`, then `python tools/check_synth.py` (fictional set with ground truth);
  - `python diagnose.py <image>` for one image;
  - `python -m cloud.runner --local DIR` for the AWS code path; it prints PARITY_RESULT.
- **Terraform:** `terraform -chdir=infra init -backend=false && terraform -chdir=infra test`, and the same for `infra/bootstrap`.
- **Deploy:** merge a PR to `main`. `.github/workflows/ci.yml` runs, then `deploy.yml`: image build, tests in the image, ECR push, `terraform apply`, and the "No drift after the apply" step. Nothing is applied from a laptop.
- **Bootstrap (IAM boundary, deploy roles):** by hand in CloudShell, `bash infra/bootstrap/cloudshell.sh`, then `... apply`.
- **Env vars** (set by Terraform; read only in `cloud/settings.py` `ENVIRONMENT`): `CALLS_LOG_BUCKET`, `CALLS_LOG_TABLE`, `CALLS_LOG_QUEUE_URL`, `CALLS_LOG_ALERTS_TOPIC_ARN`, `CALLS_LOG_RUNNER_FUNCTION`, `CALLS_LOG_INTAKE_FUNCTION`.
- **Secrets:** SSM `/calls-log/*`, set with `python3 tools/put_parameters.py` (`--passphrase`, `--owner-passphrase`, `--from-private-files`, `--sync-enabled`).
- **Upload page URL:** Terraform output `intake_url` (in the Deploy job log).

## 4. Current state
- **Git:** local branch `scrub` = `origin/main` = `ba6d3f3` ("docs: no AI attribution…"). The only uncommitted file is this one [verified]. The remote has only `main`. The session's work branch `claude/nice-ritchie-qunmoe` was deleted; recreate it from `main` if a session needs it [verified].
- **Tests:** `run_tests.py`: 357 passed, 0 failed (Linux venv, 2026-10-25) [verified].
- **CI on `ba6d3f3`:** green [verified: run 40218398926]. The privacy gate, the unit tests inside the image, offline OCR on a read-only filesystem, and "The AWS runner reads the Synthetic Set exactly like main.py" all passed.
- **Sample run here:** not possible, because this container has no PaddleOCR. CI is the evidence above.
- **Accuracy:**
  - Reference Set (3 real folders, kept outside the repo): runner image output IDENTICAL to the expected sessions, F1 3, F2 5, F3 6 sessions [from session, D-20 in `_bmad-output/project-context.md`]. Records 101/101 and 102/102 on F1/F2 [from session, D-12].
  - Synthetic Set: every call matches on two seeds [from session, D-17].
  - No per-field accuracy numbers exist [verified: none in repo].
- **Live checks:** the Owner has not confirmed the features in #24-#27 on real data [from session].

## 5. Decisions
- **OCR engine:** chose PaddleOCR (strict, no fallback) over EasyOCR, because the silent EasyOCR fallback ran for two months and loading torch plus paddle caused phantom crashes (AGENTS.md "Known pitfalls") [verified: text].
- **AWS shape:** chose "Route B" over a Fargate batch box (A) and a Step Functions per-image fan-out (C), because a queue plus one Lambda per folder is near-free at this volume and keeps the PC's order. Fargate stays a no-code fallback (`docs/aws/adr/0001-route-b-serverless-queue.md`) [verified: text].
- **Database:** chose DynamoDB over RDS/Postgres for folder status, because the needs are conditional writes, a per-day lease and an always-free tier with no VPC (ADR 0002, 0004) [verified: ADRs]. Call rows were not put in a DB, so Excel stays the record the Owner uses [from session].
- **Schema:** chose one item per upload, keyed by Person-Day, over one item per day, because re-uploads must coexist. The Held/Superseded states ("day's choice" = latest `readyAt`) let a second upload wait for a Re-run instead of replacing the day (`_bmad-output/implementation-artifacts/spec-4-6-second-upload.md`) [verified: code].
- **Secrets:** chose SSM SecureString with Terraform write-only placeholders over values in tfvars, because state then holds no secret (ADR 0008) [verified: text].
- **Cost cap:** chose a 4-hour runaway window ($1.05 worst case) over 1 h 45 min ($0.50), so a full week can run unattended (`infra/guardrails.tf` `runaway_cap_usd`) [verified: code].

## 6. Open problems
1. **Memory never benchmarked.** `runner_memory_mb = 2048` is a guess, `enable_bench` still defaults to true (`infra/variables.tf` l.34-38), and cost per image is unknown. Steps: `docs/AWS_SETUP.md` §5, `tools/bench_lambda.py`. Expected: a measured GB-s per image and a PR setting memory with `enable_bench = false` [verified: code; run status from session].
2. **Comparison Period not run.** `docs/aws/comparison-log.md` tables are empty, so PC vs AWS output was never compared on live days. Tools: `tools/compare_outputs.py`, `tools/switches.py`, `tools/turnaround.py` [verified: file empty].
3. **Rare races, accepted** [from session: review on PR #27]:
   - two Readys of the same day at the same moment both become Pending;
   - if the SQS send fails after a Re-run, the upload stays Pending with no message until Re-run is clicked again;
   - Re-runs in the same second are ordered by uploadId.

   Code: `cloud/status.py` `mark_ready`, `chosen_later`.
4. **Dependabot runner-image update closed** when `main` was rewritten (PR #28). Expected: Dependabot reopens it on its next run; if not, comment `@dependabot recreate` [verified: closed].
5. **No live AWS access from the session.** The AWS MCP connector needed a fresh sign-in, and the git proxy lets a session push only to its own branch [from session].

## 7. Safety rules
- **Ask first before changing:**
  - the Excel layout (`sprs.xlsx`, `config.py` l.168-179, `excel_writer.py`);
  - the Sheets formulas (`;` separators, numeric times);
  - `post_9am_exception_limit` (per day by the Owner's choice);
  - the Sync Switch;
  - the Web App URL and secret (CloudShell only);
  - the live Apps Script deployment (redeploy as a new version, never "New deployment");
  - the queue trigger state;
  - anything in `../HANDOFF.md` or `../DRIVE_STRUCTURE.md`.
- **Never:**
  - run `main.py`, `resync_output.py` or `tools/resync.py` on real data without the Owner's go-ahead; they overwrite live Sheet tabs and batch mode moves input folders;
  - commit employee names, phone numbers, spreadsheet IDs or screenshots;
  - add AI attribution to commits, PRs or docs (AGENTS.md rule).
- **Test safely:** `run_tests.py`, `tools/check_synth.py`, `diagnose.py`, `python -m cloud.runner --local DIR`, and Terraform `test` (plan-only, fake credentials). PRs get a Terraform plan comment without applying.
- **Roll back:**
  - Code: revert the PR on `main`; the deploy re-applies the previous code (image tag = commit SHA).
  - Stop AWS: Owner tab → AWS processing → Stop, or `docs/TECHNICAL.md` §11 "Stopping the runner by hand".
  - Switch back to the PC: turn the AWS Sync Switch off, then run the PC as before; the rehearsal is in `docs/TECHNICAL.md` §11.

## 8. Next steps
1. **Start the benchmark (under 5 minutes).** Open `tools/bench_lambda.py` `main` (l.170) and `docs/AWS_SETUP.md` §5. It runs from a machine with AWS credentials against a Synthetic Set made by `tools/gen_synth.py`. The doc is written for the PC; whether it runs in CloudShell is [unverified]. Record the GB-s per image and open a PR setting `runner_memory_mb` and `enable_bench = false` in `infra/variables.tf`.
2. Have the Owner run the live checks of PRs #24-#27 (`docs/OPERATOR_GUIDE.md`) on one real day, with sync on for one employee only.
3. Run the Comparison Period for at least 5 days and fill in `docs/aws/comparison-log.md` with label-only output.
4. Confirm the Dependabot image update reopened, then merge it after green CI.
5. Write the measured cost per run and the turnaround into `docs/aws/architecture.md` (Story 6.2).
6. Decide whether call/session rows go into a queryable store (DynamoDB items or RDS) for the portfolio's "database" story. Write an ADR first.
7. Remove the `bench` functions once item 1 is merged.

## 9. Portfolio notes
- **Worth showing:**
  - `docs/aws/architecture.md` (diagram plus flow) and the 8 ADRs in `docs/aws/adr/`;
  - `docs/aws/lessons-from-the-first-deploys.md`;
  - IaC with Terraform tests that pin IAM least-privilege (`infra/tests/*.tftest.hcl`);
  - OIDC deploys with no keys, and a permissions boundary;
  - cost guardrails with a kill switch, and a single-flight lease with checkpoints;
  - an EN/VI upload page under a strict CSP.
- **Missing:**
  - measured cost per run and per image (next step 1);
  - throughput and turnaround numbers from real days;
  - a public README section with the diagram and the trade-offs;
  - a sanitized demo (synthetic set, screenshots of the page);
  - a decision on the call-row database;
  - removal of the leftover `bench` resources.

## 10. Gotchas
- `-X utf8` on every Python run, or piped output crashes on Japanese test names.
- `import runtime_env` must stay first in `main.py`, `ocr.py`, `image_preprocess.py` and `diagnose.py`.
- `paddle_enable_mkldnn = False` and `ocr_strict_engine = True` must stay.
- Never use `\b` in `parser.py`, because CJK characters count as word characters.
- Image-only tests read `infra/` through `_infra_text()`. A test that reads `infra/` directly breaks the deploy's in-image test step, which caused the #25 deploy failure.
- New IAM rights need the bootstrap boundary first (`infra/bootstrap/main.tf`, applied by hand) or AWS refuses them at run time. The ruleset `main: pull requests with green CI` blocks direct pushes.
- The session proxy allows git pushes only to the session's branch. Owner-only steps (moving `main`, deleting branches) are done in CloudShell with a short-lived token.
- The local git identity in a fresh container may default to an AI name. Set `git config user.name/email` to the Owner's before committing.
- `pkill -f <script>` kills its own shell; stop helper servers by PID.
