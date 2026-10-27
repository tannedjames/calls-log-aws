# ADR 10: A demo environment on synthetic data, with a public dashboard

**Status:** proposed. Waits on the Owner's decisions listed at the end; nothing is built. **Covers:** Phase 6 of [the data lake plan](../data-lake-plan.md): showing the pipeline and the lake working, publicly, without any employee data.

## Context
The production pipeline can't be shown to anyone: its inputs are employees'
screenshots, and its lake, however keyed, is work data. The Synthetic Set
(`tools/gen_synth.py`) already renders fictional call-log screenshots with
known answers, and CI runs the real OCR on them. Phase 6 asks for the same
pipeline, end to end, on that kind of data every day, and a public page
showing what the lake's views say about it.

Three facts shape the design:

- **Names.** Every resource name is `calls-log-…`, and the bootstrap's
  patterns are `calls-log-*` (topics, budgets, tables, queues) or exact ARNs
  (the data bucket). The parameter path `/calls-log/` is a constant in
  `cloud/settings.py`. Resource names already reach the code only through
  `CALLS_LOG_*` environment variables (AD-9).
- **No public buckets.** Account-wide Block Public Access stays on (it lives
  in the bootstrap so the deploy role can't turn it off). A public page needs
  CloudFront in front of a private bucket.
- **The budget.** $2 a month for production, with a kill switch. The demo
  must not be able to spend production's margin.

## Decision (proposed)

**6a. One more copy of `infra/`, named `calls-log-demo`, in the same account.**
- `infra/` takes `var.env` (`calls-log` by default, `calls-log-demo` for the
  demo) and builds every name from it. Production's plan must show **no
  change** when this lands: a test pins today's names, and the PR's plan
  comment proves it.
- The demo has its own state key, bucket, Folder Status table, queue,
  functions, Glue database (`calls_log_demo`), Athena workgroup, SSM path
  (`/calls-log-demo/`, through a new `CALLS_LOG_PARAMETER_PREFIX` variable in
  `cloud/settings.py`), budget ($1) and kill switch.
- **No path from the demo to production data.**
  - The demo's roles get their own boundary, `calls-log-demo-boundary`. It
    allows only `calls-log-demo-*` resources, the demo bucket and
    `/calls-log-demo/*`.
  - Production's names never match those patterns (`calls-log-folders` is not
    `calls-log-demo-*`), and a `.tftest.hcl` test checks it.
  - The production boundary's `calls-log-*` patterns *do* match demo names.
    That direction is harmless: production code never names a demo resource.
    It would still be tightened by excluding `calls-log-demo-*` explicitly.
- **Sheets sync off for good.**
  - The demo has no Web App URL or secret parameter.
  - `cloud/settings.py` refuses sync whenever the parameter prefix isn't
    `/calls-log/`.
  - A test covers both.
- **A daily feeder.** EventBridge Scheduler calls a small handler in the runner
  image (`cloud/demo_feed.py`) once a day. It renders a Synthetic Set for 3 to
  5 fictional employees, seeded by the date. It puts the images in the demo's
  `inbox/`, then makes each folder Ready through `cloud/status.py`, exactly as
  the upload page's intake does. The real runner, publisher and lake then
  handle them.

**6b. The public page: summary JSON behind CloudFront.**
- A daily scheduled function in the demo queries the demo's views
  (`day_summary`, `weekly_attendance`, `pipeline_metrics`, `ocr_quality`, and
  `ocr_accuracy` against the Synthetic Set's own ground truth). It writes
  `summary.json` to a private site bucket.
- CloudFront serves the bucket with Origin Access Control, so Block Public
  Access stays on. The page is static HTML with a strict Content Security
  Policy (`default-src 'self'`, no inline script, no third-party hosts), and
  it reads only `summary.json`.
- The privacy gate also checks the published JSON: only the Synthetic Set's
  fictional names may appear, and nothing from production.

**6c.** A 2–3 minute recording, outside the repo.

## Options considered

| Option | Verdict | Why |
|---|---|---|
| Same account, `calls-log-demo` names, own boundary | Proposed | No new account to set up, one bootstrap apply; isolation by IAM, tested |
| A separate AWS account (Organizations) | Strongest isolation | A second bootstrap, a second budget and sign-in, an Organizations setup; worth it only if the demo should outlive this project or be handed over |
| Feed the demo from CI instead of a schedule | Rejected | CI would need write access to AWS on a timer; the schedule keeps every write inside the demo's own roles |
| GitHub Pages for the page | Possible | Free and simple if the repo is public; otherwise it needs a paid plan, and a CI job with demo read rights to publish |
| S3 static website hosting | Rejected | Needs a public bucket, which account-wide Block Public Access forbids |

## Cost (estimate, to be replaced by `pipeline_metrics`)
- Lambda: about 5 folders × 10 images a day. That is a few hundred GB-s a day,
  under 10,000 a month, inside the free tier unless production already uses
  most of it (the benchmark will tell).
- Athena: a few queries a day, each billed at the 10 MB minimum, about $0.002
  a month.
- S3, DynamoDB, SQS, EventBridge and CloudFront are pennies or free tier at
  this size.
- A custom domain would add about $0.50 a month (a Route 53 hosted zone), plus
  the domain itself.
- The demo's own $1 budget and kill switch cap it, apart from production's $2.

## Consequences
- `infra/` becomes parameterised. Every later infra change is applied to both
  environments, so the demo shows the same code as production.
- The bootstrap gains the demo boundary and deploy-role rights for the demo's
  patterns (applied by hand, [ADR 7](0007-hand-applied-bootstrap-and-role-fence.md)).
- `cloud/settings.py` gains `CALLS_LOG_PARAMETER_PREFIX`, defaulting to
  `/calls-log/`, so production is unchanged.
- The demo exercises the real pipeline daily, which is also an early warning
  for production: a broken image fails the demo the next morning.

## Decisions for the Owner
1. Build Phase 6 at all, now or later. Everything above waits on it.
2. Same account (proposed) or a separate AWS account.
3. The page's address: CloudFront's own `*.cloudfront.net` (free), a custom
   domain, or GitHub Pages (only if the repo goes public).
4. Whether the repository, or a copy of it, goes public with the page.
