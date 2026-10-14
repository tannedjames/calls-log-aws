---
title: calls-log on AWS (brownfield, no functional change)
status: final
created: 2026-10-14
updated: 2026-10-14
owner: Owner
author: John (bmad-agent-pm), Fast path
inputs:
  - _bmad-output/project-context.md
  - _bmad-output/planning-artifacts/brownfield-system-documentation.md
  - _bmad-output/planning-artifacts/risk-assessment.md
  - _bmad-output/planning-artifacts/ocr-benchmark-2026-10-14.md
---

# PRD: calls-log on AWS

*Working title. Confirm it.*

## 0. Document purpose

This PRD defines what "calls-log runs on AWS with no functional change" must deliver, so the
architecture (Step 4) and the epics and stories (Step 5) can be built from it. Its sources are
the system inventory, the risk assessment and the OCR benchmark listed in the frontmatter; this
document builds on them and does not repeat them. Features are grouped, with requirements nested
and numbered FR-1…FR-N. System-wide qualities are in §6. Terms are defined once in §3 and used
exactly. Decisions the Owner confirmed on 2026-10-14 are listed in §13. Technical options and
mechanisms are in `addendum.md`.

## 1. Vision

calls-log turns employees' phone call-log screenshots into daily calling sessions. It writes them
to an Excel Day Sheet and to each employee's Google Sheet. Today an Operator collects about 2,000
screenshots a week, files them into Person-Day Folders on one Windows PC, and runs a batch command
by hand once or twice a week. Nothing happens unless that PC is on and someone runs the command.
A full batch takes about three hours on the PC.

On AWS, the same pipeline produces the same numbers, but it runs **as soon as a folder is
uploaded**, and the Google Sheet is updated **within two hours**, without the PC. It costs **at
most $2 a month**. Failures are visible and bounded instead of silently retried. The PC stays a
fully working alternative: the Owner chooses whether the PC, AWS, or both write to the Sheets.
Beyond the CV, the Owner's motivation is per-upload turnaround without depending on one PC being
on and attended.

The migration is also the Owner's portfolio piece for Cloud/DevOps roles. Everything is defined in
Terraform and deployed by GitHub Actions through OIDC, with measured cost and turnaround. The
constraint that makes the story interesting is $2 a month for 13 employees.

## 2. Target users

### 2.1 Jobs to be done

- **Operator:** "When screenshots arrive, get them processed without babysitting a PC or a
  command line, and know straight away if something needs fixing."
- **Owner:** "Run the system in the cloud for almost nothing, never lose a day's results, and be
  able to show and defend how it was built."
- **Employees and their managers** (indirect): "See each day's calling time in my Google Sheet
  soon after I send my screenshots." Unchanged in content, faster in timing.

Operator and Owner may be the same person, so the requirements treat them as roles. If the
Operator is someone else, they are non-technical.

### 2.2 Non-users (v1)

- Employees do not upload their own screenshots. Intake stays with the Operator.
- No one other than the Owner administers AWS.

### 2.3 Key user journeys

- **UJ-1. The Operator processes a week's screenshots without the PC running a batch.**
  It's Friday afternoon and screenshots have come in all week over the chat app. The Operator
  makes a folder per person per day, named `Nguyen Van A & 2026-06-01`, and uploads each one
  from the office PC's browser. A folder whose name doesn't match a registered employee, or that
  has more than 40 images, is rejected on the spot with the reason. Once a folder is marked
  complete, processing starts. Within two hours (usually minutes) the employee's Google Sheet
  has a new day tab and the Day Sheet is stored. The Operator sees the folder change to **Done**.
  **Edge case:** the upload is interrupted half-way. Nothing is processed until the folder is
  completed, so no short day ever reaches the Sheet.

- **UJ-2. Something goes wrong and the Owner finds out, rather than the system looping.**
  One folder keeps failing because the Web App rejects it (`unknown name`). After a few attempts
  it is **Parked**, and the Owner gets an email with the folder and the reason. The Owner fixes
  the cause (for example, adds the employee to the Web App) and re-queues the folder. No money is
  spent on the parked folder in between.

- **UJ-3. The Owner picks which system writes, and can switch at any time.**
  For two weeks the PC and AWS process the same folders and their Day Sheets are compared. Sync
  is on for whichever system the Owner chooses. Every day matches, so AWS becomes the everyday
  path. Months later an AWS change misbehaves. The Owner turns AWS Sync off and runs the batch on
  the PC with Sync on, all within 30 minutes. Both systems produce identical results, so it never
  matters which one wrote a given day.

## 3. Glossary

- **Owner:** the person accountable for the system and the AWS account.
- **Operator:** the person who files and uploads screenshots. May be the Owner.
- **Person-Day Folder (Folder):** one employee's screenshots for one date, named
  `<Name> & yyyy-mm-dd`, with 1–40 images in `.jpg .jpeg .png .webp .bmp`. Most Folders have
  fewer than 20 images; some have 30–40; 40 is the maximum.
- **Ready:** a Folder whose upload the Operator has marked complete. Only Ready Folders are
  processed.
- **Run:** one processing attempt on one Folder: OCR → Sessions → Day Sheet → Sync.
- **Session:** a block of calling time built by the existing rules (`docs/TECHNICAL.md` §5).
- **Day Sheet:** the `.xlsx` produced from `sprs.xlsx` for one Folder.
- **Web App:** the existing Google Apps Script endpoint that writes a day into an employee's
  Google Sheet. Unchanged by this project.
- **Sync:** sending a Folder's Sessions to the Web App.
- **System:** the AWS deployment of calls-log.
- **Person-Day:** one employee on one date. At most one Run per Person-Day executes at a time
  (FR-6).
- **Folder Status:** `Pending` → `Processing` → `Done`, `Failed` or `Parked`. `Failed` means the
  last Run failed and a retry is scheduled; after 3 failed Runs the Folder is `Parked` (FR-10).
- **Parked:** a Folder that has failed the maximum number of Runs and is no longer retried
  automatically.
- **Sync Switch:** the on/off setting on each system (PC and AWS) that decides whether that system
  Syncs. The Owner sets each one independently: PC only, AWS only, or both.
- **Reference Set:** real Folders, with the Day Sheets the PC produced for them, supplied by the
  Owner. Kept only on the Owner's PC.
- **Synthetic Set:** generated screenshots with known ground truth. Contains no real data and is
  safe for CI.
- **Parity:** identical Sessions, and therefore an identical Day Sheet and Sync payload, for the
  same Folder.
- **Comparison Period:** the time before cutover when the PC and AWS both process the same
  Folders and their Day Sheets are compared.
- **Guardrail:** an automatic limit that stops spending or processing before the cost cap is at
  risk.

## 4. Features

### 4.1 Folder upload and intake

**Description:** The Operator gets each Folder into AWS from the office PC, without developer
tools and without long-lived AWS credentials on that PC. Bad Folders are refused at the door, so
they never cost OCR time. Processing starts only when a Folder is Ready. Realizes UJ-1.

#### FR-1: Upload a Folder
The Operator can upload a Folder (up to 40 images) from the office PC in one action, and mark it
Ready.
- An upload of 40 images completes without the Operator using a command line.
  The upload happens in the office PC's browser.
- No long-lived AWS access key exists on the PC, in any file or environment variable.
- The Operator sees the Folder Status after upload (see FR-15).

#### FR-2: Reject invalid Folders before processing
The System refuses a Folder before any OCR if:
- its name doesn't parse into a name and a **valid calendar date**;
- its name doesn't match a registered employee;
- it has no supported images or more than 40 images;
- it contains only unsupported formats such as HEIC.

It tells the Operator the reason.
- An invalid Folder uses zero OCR compute and never reaches the Web App.
- Each rejection reason is stated in plain words.
- Accepted deviation from today: the PC still writes a Day Sheet for an unknown name and keeps the
  folder, whereas AWS rejects it before OCR.

#### FR-3: Process only complete Folders
A Folder is processed only once it is Ready.
- If an upload is interrupted, nothing is processed and the Google Sheet does not change.
- A Ready Folder can't be changed. To correct a day, the Operator uploads the complete Folder
  again, which re-processes it as FR-7 does. This matches today, where re-running a date
  overwrites its tab.
- An upload never alters a Run in progress.

### 4.2 Folder processing

**Description:** Each Ready Folder goes through the existing pipeline unchanged: preprocess, OCR,
parse, dedupe, build Sessions, write the Day Sheet, Sync. The counting rules, OCR models and
settings are the ones production uses today. Realizes UJ-1.

#### FR-4: Identical results
For every Ready Folder, the System produces the same Sessions, Day Sheet contents and Sync
payload as the production PC.
- The parity gate (FR-26) passes for 100% of Reference Set Folders.
- The load-bearing settings are unchanged and checked by a test: strict OCR engine with no
  fallback, oneDNN off, post-09:00 exception limit of 4 **per day**.

#### FR-5: Turnaround
The Google Sheet is updated within 2 hours of a Folder becoming Ready, at volumes up to 2,000
images a week.
- p95 from Ready to Sync success ≤ 2 h, measured over the first 4 weeks after cutover.
- A typical Folder (under 20 images) finishes its Run in ≤ 10 min, and a maximum-size Folder
  (40 images) in ≤ 20 min. These budgets add margin to the ~11 s per image measured on a laptop
  and are re-measured in Phase 0.

#### FR-6: One Run per Person-Day at a time
Two Runs for the same employee and date never execute at the same time.
- Re-uploading or re-queuing the same Person-Day while a Run is in progress waits; it never runs
  in parallel.
- No duplicate day tab is ever created by concurrent Syncs.
- Folder names that differ only in Unicode form (for example, Vietnamese accents typed on
  different devices) count as the same Person-Day.

#### FR-7: Reprocess on demand
The Owner can re-queue any stored Folder, including Parked ones, without re-uploading.
- Earlier Day Sheets for that Person-Day are kept, never overwritten (see FR-12).
- The Google Sheet tab is overwritten, exactly as on the PC today.

### 4.3 Sync and commit rules (parity with today)

**Description:** AWS talks to the same Web App in the same way, and keeps the PC's rule that a
Folder is only finished once both the Day Sheet and the Sync have succeeded. Realizes UJ-1 and
UJ-2.

#### FR-8: Same Web App contract
The System Syncs with the existing request shape, retries and error handling.
- The payload is byte-for-byte equivalent to the PC's for the same Sessions: `secret`, `name`,
  `date`, `sessions[start,end]`.
- Up to 3 attempts with 1 s and 2 s backoff and a 60 s timeout. `ok:false` or a non-JSON reply
  counts as failure.
- No change to the Web App code. Rotating the secret value is allowed (FR-19).

#### FR-9: Commit rule
A Folder becomes Done only when its Day Sheet is stored **and** its Sync succeeded. Otherwise it
becomes Failed and another Run is scheduled (FR-10 bounds the retries).
- A Sync failure leaves the Day Sheet stored and the Folder not Done.
- A Folder that yields zero Sessions fails without storing a Day Sheet, as today.

#### FR-10: Bounded retries and parking
After 3 failed Runs a Folder is Parked: no more automatic Runs, and the Owner is alerted with the
last error.
- A Parked Folder uses no compute until it is re-queued (FR-7).
- The alert names the Folder and the failure reason.

### 4.4 Results and data retention

**Description:** Day Sheets are the record and are never lost. Screenshots are temporary. The
Owner can bring old results across and re-send them. Realizes UJ-1 and UJ-3.

#### FR-11: Get results
The Owner can list Day Sheets by employee and date and download any of them.
- Any Day Sheet can be found by employee and date and downloaded in ≤ 1 min, without the AWS
  console.

#### FR-12: Retention
- Screenshots are deleted 30 days after upload.
- Day Sheets are never deleted automatically, and are protected against accidental overwrite or
  deletion. Earlier versions can be recovered.
- Logs are kept for 30 days.
- Accepted deviation from today: retention runs 30 days from upload, whereas the PC counts 30 days
  from the folder's last modification.

#### FR-13: Re-send stored results
The Owner can re-send stored Day Sheets for an employee and/or date to the Web App, with a dry
run. This is the AWS equivalent of `resync_output.py`.
- It accepts every name and date form the processing step accepts (the current tool skips some).

#### FR-14: Bring over existing results
Before cutover, all Day Sheets in the PC's `output/` are imported into AWS once.
- The imported count equals the PC's count, and a sample of files compares byte-identical.

### 4.5 Status and alerts

**Description:** The Owner and Operator can see what happened without opening AWS consoles, and
problems come to them. Realizes UJ-2.

#### FR-15: Folder Status
The Operator and Owner can see, for each Folder, its Folder Status, last Run time and last error.
- Folder Status is visible in the same place the Operator uploads, without the AWS console.
- A change of Folder Status is visible within 5 min.

#### FR-16: Alerts
The Owner receives an email when:
- a Folder is Parked;
- the month's actual or forecast AWS cost passes 50%, 80% or 100% of $2;
- a Guardrail stops processing.

### 4.6 Cost guardrails

**Description:** $2 is a hard cap, and AWS has no native hard cap, so the System must make
overspending structurally impossible, not just reported.

#### FR-17: Monthly cost ceiling
The AWS bill for the System is ≤ $2.00 every month at 2,000 images a week.
- Measured from the AWS bill each month, starting with the first full month after cutover.
- The cost model also fits within $2 at the 12-month projection of 18 employees (about 2,800
  images a week).

#### FR-18: Runaway protection
No failure mode (retry loop, upload flood, stuck Run) can spend more than $0.50 before processing
stops automatically and the Owner is alerted.
- Proven by a test that simulates a Folder that always fails, and another that uploads many
  Folders at once.

### 4.7 Security and privacy

**Description:** Screenshots carry customers' phone numbers and Day Sheets are employees' working
hours. The Web App secret can overwrite every employee's sheet.

#### FR-19: Secret handling
The Web App secret is held only in a managed secret store and is read at Run time. It is never in
the image, the repo or the logs.
- The secret is **rotated at cutover**: a new value in the live Web App, in AWS and in the PC's
  `secrets_local.py`, and the old value stops working.
- The PC keeps reading its copy from `secrets_local.py`, so it can still Sync (FR-29).

#### FR-20: Data protection
Screenshots, Day Sheets and Folder Status records are encrypted at rest and in transit. Public access is
impossible by configuration. Only the processing identity and the Owner can read them.

#### FR-21: Least privilege, no long-lived keys
Every AWS identity has only the permissions it uses. CI authenticates through GitHub OIDC limited
to the repo's `main` branch. No long-lived access keys exist anywhere.

#### FR-22: Privacy gate that fails closed
CI blocks any change that would publish employee data or the secret, including when the private
files are absent.

### 4.8 Delivery pipeline

**Description:** The System is reproducible from code. This is also the core of the portfolio
piece.

#### FR-23: Infrastructure as code
All AWS resources are defined in Terraform.
- `terraform plan` on `main` shows no drift.
- A new account can be stood up from code plus a short documented list of manual steps (Paid-plan
  sign-up, root MFA, budget email confirmation).

#### FR-24: CI/CD
A push to `main` runs the tests, the privacy gate and the Synthetic Set check. It then builds the
container image, pushes it to ECR and deploys.
- Authentication to AWS is through OIDC only (FR-21).
- A failed gate blocks the deploy.

#### FR-25: Reproducible runtime
The runtime is built from a hashed lockfile matching production's package versions, with OCR
models baked in and their checksums recorded.
- No model is downloaded at Run time.
- The same commit builds an identical dependency set.

### 4.9 Parity verification

**Description:** "No functional change" is proven, not asserted: with real data on the Owner's PC,
and with synthetic data on every change.

#### FR-26: Golden parity test (Reference Set)
A test run on the Owner's PC compares the AWS runtime's Sessions with the PC's Day Sheets for every
Reference Set Folder. It passes only when all are identical.
- The Reference Set never leaves the Owner's PC and is never committed.
- It is run before cutover and after any dependency or model change.

#### FR-27: Synthetic regression in CI
Every change runs the Synthetic Set through the real OCR runtime and compares against its ground
truth. A mismatch blocks the deploy.

#### FR-32: Upright photos are not re-rotated (accuracy fix, shared core)
OCR applies page-orientation classification only to images that are landscape after
preprocessing. Portrait photos and screenshots are processed as upright.
- Reference Set folder 3, image 3 (an upright photo the classifier rotated 90°) yields its 7
  calls.
- All three Reference Set folders then produce the expected Sessions. For folder 3 that is the
  rule-based result the Owner confirmed: 08:01–09:57, 11:22–12:01, 12:36–13:48, 13:54–14:17,
  14:23–14:23, 14:30–15:01.
- A landscape (sideways) photo still gets orientation classification.
- The change is in the shared pipeline core, so the PC and AWS behave identically (NFR-8).

### 4.10 Cutover and choice of writer

**Description:** Both the PC and AWS can write to the Sheets, and the Owner chooses which one
does, at any time. This is safe because both are held to Parity: when both process the same
Folder, the later Sync overwrites the day with identical values. Realizes UJ-3.

#### FR-28: Comparison Period
Before cutover, the PC and AWS process the same Folders for 2 weeks and their Day Sheets are
compared. Sync is on for whichever system or systems the Owner chooses.
- Cutover requires 100% Parity across the Comparison Period.

#### FR-29: Choose the writer
The Owner can turn Sync on or off on the PC and on AWS independently: PC only, AWS only, or both.
- Each Sync Switch is one documented change and takes effect for the next Run.
- The current setting of both switches is visible in one place (the runbook or Folder Status).

#### FR-30: Switch at any time
The Owner can move processing back to the PC (PC Sync on, AWS Sync off) within 30 minutes, at any
time, with no end date.
- The switch is rehearsed once before cutover.
- After a dependency or model change on either side, FR-26 is re-run before both systems are
  trusted to write again.

### 4.11 Portfolio deliverables

**Description:** The Owner's employability goal, delivered as artefacts that come out of the work
anyway.

#### FR-31: Case-study pack (private for now)
The repo contains:
- an architecture diagram;
- the decision log (ADRs);
- a measured cost and turnaround report;
- the runbook;
- the Synthetic Set generator.

All of it is free of private data, so it can be published later without edits (E2).

## 5. Constraints and guardrails

| Constraint | Value | Source |
|---|---|---|
| Cost | $2.00/month hard cap; AWS **Paid plan**, because the Free plan closes the account after 6 months | owner 1a; risk ADV-18 |
| Region | `ap-southeast-1` (Singapore). Fall back to `us-east-1` if the cost model is within 20% of the cap | owner 1b |
| IaC | Terraform | owner E3 |
| Code hosting and deploy | Private GitHub repo; GitHub Actions via OIDC → ECR | owner Q7, owner flow |
| Google side | Web App code unchanged; only the secret value rotates | owner Q3 |
| Repo privacy | No employee data, names, IDs, URL or secret in tracked files | `AGENTS.md` |
| OCR invariants | Strict engine, no fallback, oneDNN off, `runtime_env` imported first | `AGENTS.md` |
| Business rules | Unchanged, including the per-day exception limit | `AGENTS.md`, owner |

## 6. Cross-cutting non-functional requirements

- **NFR-1 Availability:**
  - No uptime SLA. Processing runs whenever the region is healthy, and a failed Run can be
    recovered the same day.
  - **Switch-back time:** 30 min to move processing to the PC, at any time (FR-30).
  - **Data loss:** zero Day Sheets lost.
- **NFR-2 Performance:** see FR-5. Cold starts are acceptable within the 2 h target.
- **NFR-3 Cost:** see FR-17 and FR-18. Every resource must be pay-per-use. Nothing may carry a
  fixed monthly fee unless the cost model shows it fits.
- **NFR-4 Security baseline:**
  - FR-19 to FR-22;
  - root account MFA, with no root access keys;
  - billing alerts on.
- **NFR-5 Observability:**
  - every Run logs Folder, Folder Status, duration and outcome, and never logs OCR row text;
  - failure and duration metrics are available to alerting;
  - logs are kept 30 days.
- **NFR-6 Reproducibility and maintainability:**
  - one-command deploy from `main`;
  - the existing 81 tests keep passing;
  - new tests cover the orchestration rules ported from `main.py`, the Day Sheet writer and the
    config invariants (risk VG-02 to VG-05).
- **NFR-7 Data governance:**
  - all data stays in the chosen region;
  - screenshots are kept 30 days, logs 30 days, Day Sheets indefinitely;
  - classification: PII (customer phone numbers) and employee working hours.
- **NFR-8 PC stays a full alternative:** the PC path keeps working unchanged with no end date, on
  the same pinned versions as AWS, so either system can write (FR-29).

## 7. Non-goals

- No change to OCR, preprocessing, parsing, counting rules or the Day Sheet layout. That includes
  the known limitations: yesterday's rows, the 16-session cap in the Sheets, and the per-day
  exception budget. **One Owner-approved exception:** the photo-orientation accuracy fix (FR-32),
  which applies to the PC and AWS alike.
- No change to the Web App code (including moving the secret to Script Properties), the employee
  map, or the Google Sheets layout.
- No web app for employees, no employee self-upload, no dashboard beyond Folder Status.
- No Kubernetes, load balancer, NAT gateway, database server or always-on machine. None serves a
  function, and each breaks the cap.
- No Windows servers on AWS.
- Developer tools stay on the PC: `diagnose.py`, single-folder mode and `create_template.py`. The
  Web App's maintenance functions stay manual in the Apps Script editor.
- No multi-region or disaster-recovery region.
- No public repo or public case study yet (E2: "maybe later").

## 8. MVP scope

### 8.1 In scope
FR-1 to FR-31, through the rollout in §10.

### 8.2 Out of scope for the MVP
- Moving the Web App secret to Script Properties or adding request signing (needs a Web App code
  change; a candidate for after the migration).
- A public case-study repo (deferred, E2).
- Fixing known limitations such as yesterday's rows or the 16-row cap (that would be a
  functional change).
- Decommissioning the PC's OCR setup (it stays a working alternative writer, NFR-8).

## 9. Risks and mitigations

Full register: `risk-assessment.md`. Each deployment blocker maps to the requirements that close
it:

| Risk | Closed by |
|---|---|
| B1 OCR parity not provable | FR-4, FR-25, FR-26, FR-27 |
| B2 no "folder finished uploading" signal | FR-3 |
| B3 rename-as-commit has no S3 equivalent | FR-9, FR-10, FR-15 |
| B4 credentials baked into the image | FR-19 |
| B5 Lambda filesystem and model downloads | FR-25 |
| B6 OpenCV system libraries | FR-25, FR-27 |
| B7 unbounded retries vs the cap | FR-10, FR-18 |
| B8 no operator upload path | FR-1, FR-21 |

**Accepted risk: both systems writing (risk ADV-17).** The Owner wants to be able to write from
either system (FR-29). If the PC and AWS ever disagree, whichever Syncs last wins, and the Sheet
flip-flops. Separately, if both systems Sync the same Person-Day at the same moment, the Web App
can create a duplicate day tab: FR-6 serialises Runs within AWS only.

Mitigations:
- both run the same pinned versions (FR-25, NFR-8);
- FR-26 is re-run after any change on either side (FR-30);
- the runbook rule: a given Person-Day is processed by one system at a time.

## 10. Rollout

1. **Phase 0, parity spike:**
   - pin to production's versions (OQ-1);
   - build the Linux runtime;
   - re-run the benchmark there;
   - pass FR-26 on the Reference Set.
   - **Exit:** parity 100%, and the cost model fits within $2.
2. **Phase 1, build:** Terraform, pipeline, intake, processing, Folder Status, alerts, Guardrails.
3. **Phase 2, Comparison Period:**
   - 2 weeks of the PC and AWS processing the same Folders, with Sync on wherever the Owner
     chooses;
   - import past results (FR-14);
   - rehearse the switch-back (FR-30).
4. **Phase 3, cutover:** rotate the secret on all three holders (Web App, AWS, PC). AWS becomes
   the everyday path.
5. **Phase 4, steady state:** the PC stays ready as an alternative writer with no end date. The
   Owner closes the migration once SM-1 to SM-4 hold.

## 11. Success metrics

**Primary**
- **SM-1 Parity:** 100% of Reference Set Folders and 100% of Comparison Period Folders produce
  identical Sessions. Validates FR-4, FR-26, FR-28.
- **SM-2 Turnaround:** p95 from Ready to Sync success ≤ 2 h over the first 4 weeks after cutover.
  Validates FR-5.
- **SM-3 Cost:** AWS bill ≤ $2.00 in each of the first 3 full months. Validates FR-17 and FR-18.

**Secondary**
- **SM-4 No lost results:** 0 Day Sheets lost or overwritten. Validates FR-9 and FR-12.
- **SM-5 Operator effort:** weekly hands-on time at or below today's. The baseline is measured
  during the Comparison Period. Validates FR-1 to FR-3.
- **SM-6 Switch-back ready:** the rehearsal completes in ≤ 30 min before cutover. Validates FR-30.

**Counter-metrics (do not optimise)**
- **SM-C1 Cheaper OCR:** never lower cost by switching to smaller models or lower resolution.
  Parity wins over SM-3.
- **SM-C2 More parallelism:** never cut turnaround by raising concurrency past the Guardrails.
  Cost safety wins over SM-2.
- **SM-C3 Service count:** never add AWS services for the CV. Every component must serve a
  requirement in §4.

## 12. Open questions

1. **OQ-1** What are production's exact versions? Needed: `pip freeze` and the
   `.paddlex\official_models` listing from the PC. *Blocks FR-25 and Phase 0.*
2. **OQ-2** Reference Set delivery: 5–10 days, screenshots and photos, with the PC's Day Sheets, at
   `C:\Users\admin\Downloads\calls-log\parity-reference\`. *Blocks FR-26.*
3. **OQ-4** Upload mechanism behind the browser upload (FR-1). *Architecture, Step 4.*
4. **OQ-5** Where the list of registered names for FR-2 lives in AWS. It is private and must stay
   out of the repo. *Architecture, Step 4.*
5. **OQ-6** The alert email address. Set at deploy time, never in the repo.

*Resolved on 2026-10-14:* OQ-3 (Operator: treated as a role, non-technical if not the Owner) and
OQ-7 (unknown names rejected before OCR) were settled by the confirmations below.

## 13. Decisions confirmed by the Owner (2026-10-14)

The Owner corrected two items in the Fast-path draft and accepted the rest by not objecting.

| # | Decision | Where |
|---|---|---|
| A1 | Motivation beyond the CV: per-upload turnaround, not depending on the PC | §1 |
| A2 | Operator and Owner are roles; a separate Operator is non-technical | §2.1 |
| A3 | Upload through the office PC's browser | FR-1 |
| A4 | Unknown names rejected before OCR (accepted deviation) | FR-2 |
| A5 | **Corrected by the Owner:** most folders have under 20 images, some 30–40, 40 at most. Budgets: ≤ 10 min typical, ≤ 20 min for 40 images | FR-5, Glossary |
| A6 | 3 failed Runs, then Parked | FR-10 |
| A7 | Screenshot retention 30 days from upload (accepted deviation) | FR-12 |
| A8 | Alerts by email | FR-16 |
| A9 | Runaway limit $0.50 | FR-18 |
| A10 | **Corrected by the Owner:** AWS may write to the Sheets. The Owner chooses PC, AWS or both, at any time (replaces "single writer" and "AWS Sync off") | FR-28 to FR-30, NFR-8 |
| A11 | Operator-effort baseline measured during the Comparison Period | SM-5 |
