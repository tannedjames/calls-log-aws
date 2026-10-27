# calls-log Data System Plan

Plan of Oct 26, 2026. The decision is [ADR 9](adr/0009-call-rows-in-an-s3-data-lake.md),
and the rows are defined in the [data contract](data-contract.md). Ticked boxes
are built; the notes under a phase say where the build differs from the
first draft.

## 1. Store decision (ADR 9)

Call and session rows go into an S3 data lake (Parquet + Glue Data Catalog +
Athena). Status: accepted (the Owner agreed on 2026-10-26).

- DynamoDB `calls-log-folders` stays the status store.
- Excel and Google Sheets stay the Owner's official record; the lake is additive only.

| Option | Verdict | Reason |
| --- | --- | --- |
| S3 Parquet + Glue + Athena | Chosen | Serverless, no VPC, pay per query, real SQL, fits the $2 budget |
| RDS / Aurora Postgres | Rejected | Needs a VPC and paid networking (about $7–30/month) |
| DynamoDB items | Rejected | Poor for SQL, aggregation and joins |

Accepted trade-offs: there are no constraints, so the publisher runs quality
checks; queries take seconds, which is fine for daily and weekly reporting.

## 2. Layers

Four layers, two of which already exist.

| Layer | Location | Content | Retention |
| --- | --- | --- | --- |
| raw | `inbox/<uploadId>/` | Images | 30 days (exists) |
| bronze | `work/` | OCR checkpoints | 30 days (exists) |
| silver | `lake/calls/`, `lake/sessions/`, `lake/uploads/` | Validated Parquet | Permanent |
| gold | Athena views | `day_summary`, `weekly_attendance`, `pipeline_metrics`, `ocr_quality` | n/a |

`staging/` (the runner's hand-over, 30 days) sits between bronze and silver.

## 3. Data contract

The contract lives in [`docs/aws/data-contract.md`](data-contract.md) as
`schema_version = 1`.

| Table | Columns |
| --- | --- |
| calls | `person_key`, `work_date`, `upload_id`, `image_key`, `call_seq`, `start_ts`, `end_ts`, `duration_s`, `direction`, `call_status`, `countable`, `row_conf`, `start_conf`, `duration_conf`, `flagged`, `flag_field`, `flag_reason`, `parser_version`, `ocr_model`, `ingested_at` |
| sessions | `person_key`, `work_date`, `upload_id`, `session_no`, `start_ts`, `end_ts`, `span_min`, `call_count`, `source`, `ingested_at` |
| uploads | `upload_id`, `person_key`, `work_date`, `image_count`, `attempts`, `ready_at`, `started_at`, `finished_at`, `status`, `runner_memory_mb` |

Added to the first draft: `direction`, `call_status`, `countable` and
`row_conf` on calls. The Synthetic parity test needs them, attendance views
need them, and the 0.85 threshold applies to the row's confidence.

Privacy rules:

- `person_key` is an HMAC of the employee's NAME_TO_ID key, salted with SSM
  `/calls-log/person_salt`.
- No names, phone numbers, OCR text or file names anywhere in the lake.
  `image_key` is the image's position in the PC's order, and `flag_reason` is
  a closed set of codes.
- The privacy gate (`tools/check_private_data.py`) covers the lake code, as it
  covers every tracked file.

## 4. Flow

The runner hands off records; a separate publisher validates and stores them.

1. **Runner emit** (`cloud/runner.py`, after the upload is marked Done, so
   the publisher's Done check never races it)
   - Writes JSON to `staging/<person-day-key>/<uploadId>.json`.
   - Best-effort: a failure logs and alerts but never fails the run.
   - Gated by SSM `/calls-log/lake_enabled`, default off.
   - No new dependencies in the OCR image.
2. **Publisher Lambda** (zip, AWS SDK for pandas layer, S3 event on `staging/`)
   - Checks the upload is the day's choice and Done; otherwise drops it.
   - Runs the quality checks (`lake.check()`, already built).
   - Writes Parquet to the fixed key `lake/<table>/work_month=YYYY-MM/<person_key>_<date>.parquet`, so Re-runs overwrite (idempotent).
3. **Glue tables** in Terraform with partition projection on `work_month`; no crawler.
4. **Athena workgroup** with a bytes-scanned limit; results expire after 7 days.

## 5. Quality checks

The publisher is the lake's guard, since Athena accepts any row.

- `start_ts <= end_ts` (`<=` because a 0-second call ends the minute it
  starts); duration consistent within 1 minute
- No overlapping sessions; span total equals Excel B2, read back from the
  stored Day Sheet
- At least 1 call per session; the sessions' calls add up to the countable calls
- Low-confidence share under a set limit (30% to start)

Failures go to `quarantine/`, increment a CloudWatch metric, and alarm via
`calls-log-alerts`.

## 6. Phases

Seven phases; only Phase 3 touches the live flow, and only best-effort.

### Phase 0: Decide

- [x] ADR 0009 and the data contract
- [x] Owner's okay on permanent storage of work data (2026-10-26)
- [ ] Add Glue, Athena and publisher IAM rights to the bootstrap boundary (CloudShell, by hand)

Note: the bootstrap rights move into the Phase 2 pull request, so they match
the resources exactly. Phase 1 needs none: the workload boundary already lets
the runner write to the data bucket and read `/calls-log/*`. Phase 2 turned
out not to need a boundary change either, only deploy-role and plan-role
rights: the publisher's needs (the data bucket, the folders table, its own
logs) are already inside the boundary, and its metrics are log lines.

### Phase 1: Emit records (switch off)

- [x] Emitter in the runner, with per-field confidences
- [x] `--local DIR` writes the JSON locally (`--emit OUT`)
- [x] Parity test: emitted calls and sessions equal Synthetic Set ground truth (`tools/check_lake_parity.py`, run by CI in the runner image)
- [x] `run_tests.py` and the privacy gate pass
- [x] PR merged and deployed (#29, 2026-10-26)

Built with it: `/calls-log/lake_enabled` (starts `false`) and
`/calls-log/person_salt` (set once with `put_parameters.py --new-person-salt`),
the runner's right to write `staging/*`, and a 30-day expiry on `staging/`.

### Phase 2: Infrastructure

- [x] Terraform for the prefixes, publisher, Glue database and tables, Athena workgroup, quarantine alarm, budget coverage (`infra/lake.tf`; the $2 budget has no filter, so it already covers Glue, Athena and the publisher)
- [x] `.tftest.hcl` tests pinning the publisher's least-privilege IAM (`infra/tests/lake.tftest.hcl`), plus `tests.py` checks via `_infra_text()` that the Glue columns equal `lake.COLUMN_TYPES`
- [x] `tools/check_lake_parquet.py` in CI: the publisher's Parquet reads back with the Glue tables' exact columns and types
- [ ] Bootstrap applied by hand in CloudShell (1 to add, 3 to change)
- [ ] Deploy through the PR; no drift
- [ ] Replay `staging/` once, so the days staged before the publisher reach the lake

### Phase 3: Turn on

- [x] `lake_enabled = true` (set 2026-10-26, with the salt)
- [x] A Re-run of a real day (2026-10-21) reached Athena: 95 calls, 8 flagged
- [x] `tools/check_lake_choice.py`: lists every day whose lake file isn't its chosen Done upload (stale, missing, quarantined, orphan); read-only, from CloudShell
- [ ] Over a week: check that Re-runs overwrite and Superseded uploads never appear (run the check above after a week of Re-runs)

### Phase 4: Backfill

- [x] Read-only script parses the current `results/*.xlsx` (rows 7–22, columns B–C) into `sessions` with `source = 'xlsx_backfill'` (`tools/backfill_lake.py`); never overwrites a day the publisher wrote
- [x] Never runs `main.py`, never touches the Sheets
- [ ] Run once from CloudShell (docs/TECHNICAL.md §11)

### Phase 4.5: OCR accuracy

- [x] The private `labels` table (Glue, `lake/labels/`) and `tools/label_calls.py`: `sample` zips a worksheet of 20 recent calls (half flagged or low-confidence) with their screenshots, numbered rather than named; `import` checks the filled worksheet and writes the labels
- [ ] Ground truth: weekly spot-check of about 20 real calls (needs someone to label them); the Synthetic Set is already exact in CI (`check_lake_parity.py`), the Reference Set stays on the PC
- [x] `ocr_quality` view, label-free half: flagged and low-confidence counts per field, by day, person, model and parser version
- [x] `ocr_accuracy` view: exact-match rate per field and mean duration error, by model, parser version and confidence band; `sql/examples/threshold_calibration.sql` shows the exact rate per 0.05 of confidence
- [ ] Calibrate the 0.85 threshold and the 30% low-confidence limit (needs a few weeks of labels)
- [ ] Report: `docs/aws/ocr-accuracy.md`

### Phase 5: Gold layer and portfolio

- [x] Views: `day_summary`, `weekly_attendance`, `pipeline_metrics` (turnaround p50/p95, images per day, estimated GB-s per image) in `sql/views/`, created by `tools/lake_views.py`, checked in CI on DuckDB
- [x] `sql/` folder of example queries, ERD (data contract), lake diagram in `docs/aws/architecture.md`
- [x] README section on the data lake
- [ ] Real aggregate production numbers (cost, turnaround, failure rate, OCR accuracy) after a month
- [ ] Stretch: dbt-athena with dbt tests

### Phase 6: Demo environment and public dashboard

Design proposed in [ADR 10](adr/0010-demo-environment-and-public-dashboard.md);
it waits on the Owner's go-ahead and three choices (same account or not, the
page's address, a public repo). Nothing below is built.

- [ ] 6a. Same Terraform with `env = "demo"`: separate prefix, table, Glue database, workgroup, SSM; own $1 budget and kill switch; Sheets sync permanently off
- [ ] 6a. Daily scheduled job runs `gen_synth.py` for 3–5 fictional employees and feeds the real pipeline end to end
- [ ] 6b. Lambda runs gold queries on the demo lake daily and writes summary JSON; static public page (strict CSP) shows fake attendance, pipeline health, synthetic OCR accuracy
- [ ] 6c. 2–3 minute walkthrough recording
- [ ] Safety: no IAM path from demo to production data; privacy gate checks demo outputs; dashboard never reads the production lake

## 7. Sequencing, done criteria, open items

**Sequencing with the handoff's next steps**

1. Benchmark and Owner live checks of PRs #24–#27
2. Phases 0–2 in parallel
3. Phase 3 alongside the Comparison Period
4. Phases 4, 4.5 and 5
5. Phase 6 once the gold views are stable
6. Remove the bench functions

**Done when**

- Every Done day's choice reaches the lake within minutes; Re-runs replace it
- The Synthetic parity test passes in CI
- Zero personal identifiers reach the lake
- Athena and S3 cost stays under $0.20 a month
- Per-field OCR accuracy is stated with a sample size; the 0.85 threshold is confirmed or adjusted by PR
- The demo runs daily, unattended, for 7 straight days; the public dashboard shows only synthetic data
- The README shows the diagram, trade-offs, sample queries, and measured turnaround and cost (aggregates only)

**Open items**

- [x] Owner's okay on permanent storage (2026-10-26)
- [ ] Whether to backfill PC-era history (needs the Owner's go-ahead)
- [ ] Who does the weekly spot-check labeling
- [ ] Demo URL: AWS default or a custom domain
