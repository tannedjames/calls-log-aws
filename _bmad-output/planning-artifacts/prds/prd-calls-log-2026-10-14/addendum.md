---
title: calls-log on AWS — PRD addendum (technical context for Step 4)
updated: 2026-10-14
---

# Addendum: technical context for the architect

This file holds material that informs the architecture but is not a requirement. The requirements
are in `prd.md`, and this file does not decide anything.

## Measured and derived figures

- **OCR cost (laptop, synthetic set; `ocr-benchmark-2026-10-14.md`):** about 11 s wall and
  7 CPU-s per screenshot, 740 MB peak memory, 3–5 s engine start-up. The photo path costs about
  the same.
- **Volume:** 2,000 images a week, about 8,700 a month. Most Folders have under 20 images, some
  30–40, and 40 is the cap, so expect roughly 400–500 Folders (Runs) a month. More Runs means
  more engine start-ups (3–5 s each), which is negligible next to the OCR time.
- **Lambda at 2,048 MB:** about 196,000 GB-s a month, against an always-free allowance of
  400,000. That allowance is shared across the account, INIT time is billed, and the headroom has
  not been verified on AWS (risk ADV-04).
- **Fargate Spot at 1 vCPU / 2 GB:** about 27 task-hours, roughly $0.40 a month at us-east-1
  rates, plus a public IPv4 address while running.
- **Runaway rate:** one 2 GB Lambda beyond the free tier costs about $0.12/hour. AWS budgets
  refresh up to three times a day, so the Guardrail in FR-18 cannot rely on Budgets alone.

## Mechanisms named by the risk assessment (options, not decisions)

| Requirement | Options to weigh in Step 4 |
|---|---|
| FR-3 Ready | `_READY` marker or manifest uploaded last; S3 event filtered on that key; or a debounce |
| FR-6 single flight | SQS FIFO with `MessageGroupId = norm(name)+"|"+date`; or a conditional-write lock object |
| FR-9 / FR-15 Status | Per-Folder status object in S3 with conditional writes (`If-None-Match`/`If-Match`), or a DynamoDB table (always-free 25 GB) |
| FR-10 parking | `maxReceiveCount` + dead-letter queue; parking = DLQ entry plus a status update |
| FR-12 retention | S3 lifecycle (30 days, inbox prefix); versioning + no-expire rule (results prefix); log-group retention 30 days |
| FR-18 Guardrail | Reserved concurrency 1–2; function timeout; CloudWatch alarm on invocations/duration (minutes) → SNS email + a deny policy or zero concurrency; budget action as last resort |
| FR-19 secret | SSM Parameter Store SecureString (standard tier, free) read at cold start; env-var path in `config.py` with `secrets_local.py` fallback |
| FR-21 OIDC | Trust condition `sub = repo:<owner>/<repo>:ref:refs/heads/main`, `aud = sts.amazonaws.com`; permissions: push to one ECR repo, update one function, Terraform state bucket |
| FR-25 runtime | Lockfile from production `pip freeze` (`pip-compile --generate-hashes` or `uv lock`); models baked; `PADDLE_PDX_CACHE_HOME=/tmp/paddlex`; `PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True`; `mesa-libGL` + `glib2` (AL2023) or `libgl1` + `libglib2.0-0`; thread counts from the task size |
| FR-1 upload | Static upload page + presigned PUT URLs from a small function (needs an auth decision), or `aws s3 sync` via IAM Identity Center short-lived credentials |

## Code touch points expected (not a design)

- `config.py`: env/SSM credential path with fallback; env-driven thread counts; nothing else.
- A new entry point that runs exactly one Folder against object storage, reusing `extract_calls`,
  `dedupe`, `build_sessions`, `write_sessions` and `sync_day` unchanged.
- `resync_output.py`: shares `parse_name_date` and reads the configured sheet (risk EC-07b, EC-08).
- New tests: orchestration rules, Day Sheet writer, config invariants, credential loader, privacy
  gate (risk VG-02 to VG-06).
