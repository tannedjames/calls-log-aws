# ADR 9: Call and session rows go into an S3 data lake

**Status:** proposed. It waits on the Owner's okay to keep work data permanently. Phase 1 (records staged behind a switch that starts off) is built; nothing is kept until the switch is turned on. **Covers:** reporting on calls, sessions and the pipeline; measuring OCR accuracy; the case study's database.

## Context
The Day Sheet (`results/<Person-Day>.xlsx`) is the only copy of a day's calls
and sessions. DynamoDB `calls-log-folders` tracks uploads, not rows
([ADR 4](0004-single-flight-lease.md)). That leaves no way to ask "how many
hours did each person work last week", "how long does a day take from Ready
to Done" or "how often does OCR misread a duration" without opening
workbooks one by one. The store must fit inside the $2 budget, must not need a
VPC ([ADR 2](0002-no-vpc.md)), and must not change what the Owner already
relies on.

## Decision
Call and session rows go into an S3 data lake: Parquet files in the data
bucket, described by Glue Data Catalog tables, queried with Athena.

| Option | Verdict | Why |
|---|---|---|
| S3 Parquet + Glue + Athena | Chosen | Serverless, no VPC, pay per query, real SQL, fits the $2 budget |
| RDS / Aurora Postgres | Rejected | Needs a VPC and paid networking, about $7–30 a month |
| DynamoDB items | Rejected | Poor at SQL, aggregation and joins |

- The lake is additive. DynamoDB stays the status store; the Day Sheet and the
  Google Sheet stay the Owner's record. Nothing reads the lake back into the
  pipeline.
- The runner hands records over and a separate publisher stores them. After
  a Run is Done, and only while `/calls-log/lake_enabled` is `true`, the
  runner writes one JSON envelope to `staging/<Person-Day>/<uploadId>.json`.
  The hand-over is best-effort: a failure is logged and emailed, and the Run
  stays Done. The publisher (Phase 2) keeps an envelope only if its upload is
  Done and still the day's choice, runs the quality checks, and writes Parquet
  to a fixed key per person and day, so a Re-run overwrites it. Failures go to
  `quarantine/` and raise an alarm.
- The contract is [the data contract](../data-contract.md), `schema_version = 1`.
  `cloud/lake.py` builds and checks the envelope, and a test keeps the two in
  step.
- No name, phone number, OCR text or file name enters the lake. A person is
  `person_key`, an HMAC of their NAME_TO_ID key under the secret
  `/calls-log/person_salt`. An image is its position in the PC's order.
  Warnings become a closed set of codes.
- Glue tables are defined in Terraform with partition projection on
  `work_month`; there is no crawler. Athena runs in its own workgroup with a
  bytes-scanned limit, and its results expire after 7 days.

## Consequences
- Athena enforces no constraints, so the publisher is the lake's guard. It
  checks times, durations, overlaps, the Day Sheet's B2 total and the share of
  low-confidence rows before anything is written.
- Queries take seconds, which suits daily and weekly reporting.
- The cost target is under $0.20 a month for Athena and S3 together.
- Keeping work data with no end date is the Owner's call. Until they agree,
  the switch stays off. `staging/` expires after 30 days like `inbox/` and
  `work/`, and its keys carry the Person-Day the same way `results/` does.
- The salt is set once (`tools/put_parameters.py --new-person-salt`, which
  refuses to replace it). A new salt would split every employee's history.
- Per-field OCR confidences (`start_conf`, `duration_conf`) now come out of
  the shared core, so accuracy can be measured per field. Excel and Sheets
  output is unchanged: `CallRecord.to_dict()` leaves them out.
- Reverting the change that added them: an upload first processed after it
  carries the new fields in its `work/` checkpoints, and an older runner
  rejects those. Before re-running such an upload on the older code, delete
  its `work/<uploadId>/`, or wait for the 30-day expiry. The new runner
  ignores fields it doesn't know, so later additions don't have this problem.

## Phases
0. Decide: this ADR, the contract, and the Owner's okay. Built, except the okay.
1. Emit records with the switch off. Built: `cloud/lake.py`; the runner's
   hand-over; `--local DIR --emit OUT`; `tools/check_lake_parity.py` in CI.
2. Infrastructure: the publisher, Glue, Athena, the quarantine alarm, budget
   coverage, and the bootstrap rights they need (applied by hand first,
   [ADR 7](0007-hand-applied-bootstrap-and-role-fence.md)).
3. Turn on during the Comparison Period; check that Re-runs overwrite and
   Superseded uploads never appear.
4. Backfill the versioned `results/*.xlsx` into `sessions` with
   `source = 'xlsx_backfill'`; then OCR accuracy (4.5).
5. Gold views, sample SQL and the case-study numbers.
6. A demo environment on synthetic data with a public dashboard.

The full roadmap is in [the data lake plan](../data-lake-plan.md).
