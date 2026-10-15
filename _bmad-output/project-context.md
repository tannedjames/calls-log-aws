---
project: calls-log
created: 2026-10-15
owner: Owner
bmad_version: 6.12.0
track: BMad Method (module `bmm`)
enhancement: Deploy this repo on AWS with no functional change
status: draft — waiting on the owner's Context section
---

# Project context: calls-log AWS migration

Planning context for the brownfield BMAD flow (Steps 0–5). Agent instructions for working in the
code stay in `AGENTS.md`. This file holds only the migration's constraints and decisions.

## BMAD setup (Step 0)

- BMAD 6.12.0 was already installed on 2026-10-04 (`_bmad/_config/manifest.yaml`). The modules
  are `core` and `bmm` (BMad Method). No Quick Flow module is installed.
- The output folder is `_bmad-output/` (`_bmad/config.toml` → `output_folder`), not `bmad-output/`.
  Planning artifacts go in `_bmad-output/planning-artifacts/`.
- 6.12 has no init command and no track picker; the install step already did both. The
  "BMad Method" track is the `bmm` module, and this flow uses only its planning skills.
- The skills you asked for map to the installed ones like this:

| Step | Asked for | Installed in 6.12 | Notes |
|---|---|---|---|
| 0 | BMAD init, BMad Method track | (already installed) | This file stands in for `project-context.md` |
| 1 | Architect → document-project | `bmad-agent-architect` (Winston) | `document-project` is retired. Its replacement, `bmad-project-context`, only maintains the short AGENTS.md block, so Winston writes the inventory directly |
| 2 | QA / Test Architect → risk assessment | **not installed** (TEA module absent) | Needs a decision; see blocking questions |
| 3 | PM → brownfield PRD | `bmad-agent-pm` → `bmad-prd` (create) | |
| 4 | Architect → brownfield architecture | `bmad-agent-architect` → `bmad-architecture` (create) | Its decision record is the memlog (`.memlog.md`) |
| 5 | PM/SM → epics and stories | `bmad-create-epics-and-stories` | Stop after this step |

## Owner constraints (from the Context section)

> The paste referred to a "Context section" and a "spec below". Neither exists (owner, 1k). The
> constraints below come from the owner's answers to question 1 on 2026-10-14.

| # | Constraint | Value | Source |
|---|---|---|---|
| 1a | Monthly AWS budget | **$2.00/month, hard cap.** Running on the operator PC costs $0, so AWS must stay near free | owner |
| 1a | Volume | **About 2,000 images per week** (≈ 8,700 a month), spread across many `<Name> & <date>` folders, each still ≤ 40 (`config.py:34`) | owner, confirmed 2026-10-14 |
| 1b | AWS region | ~~`ap-southeast-1` (Singapore)~~ **`ap-southeast-2` (Sydney)** since D-22: the AWS project's region | owner accepted the proposal; changed by the Owner 2026-10-15 |
| 1c | Employees | **13 now, about 18 in 12 months.** The "8 sheets" comment in `WebAppSync.gs:394` is out of date | owner |
| 1d | Run trigger | **Once or twice a week, started by hand today. Owner prefers one run per upload if it doesn't raise cost** | owner |
| 1e | Turnaround | **≤ 2 hours** from upload to the Google Sheet | owner |
| 1f | Failure tolerance | Default: a rerun later the same day is fine. xlsx outputs must never be lost; screenshots may expire after 30 days | default |
| 1g | AWS account | Default: new standalone account, owner is admin. No AWS Organization, so no SCPs | default |
| 1h | Operator / alerts | Default: one non-technical operator, alerts by email | default |
| 1i | Compliance | **No legal constraint beyond encryption at rest and in transit, plus the 30-day image retention** | owner chose the default |
| 1j | Rollback | Default: the Windows PC stays usable as a fallback for 4 weeks after cutover | default |
| 1k | Written spec | **None.** Work from the phase names in the owner's first message | owner |
| 2 | Deployment shape | **Screenshots are uploaded to S3, and each finished folder upload starts a run.** An always-on Windows VM is ruled out by the cap | follows from 1a, 1d |
| 2b | xlsx consumers | **Only the owner.** Results land in S3 and the owner downloads them as needed | owner accepted default |
| 3 | Apps Script | **Stays on Google, unchanged.** AWS calls the same `/exec` endpoint | owner accepted default |
| 4 | Phase 7 "DB migration" and "DNS/TLS cutover" | Rewritten as **move `output/` and the archive into S3** and **switch from the PC to AWS** (secret, Web App URL, upload method). There is no database or owned domain | owner accepted default |
| 5 | Step 2 method | **Winston runs the risk assessment with the installed `bmad-review` lenses.** The TEA module is not installed | owner accepted default |
| 6 | Parity reference data | **The owner supplies real screenshots and their PC-produced outputs.** They are kept outside the repo, on this PC only: never committed, never uploaded to AWS during planning | owner |
| 7 | Git hosting | **Private GitHub repo under the owner's account** | owner accepted default |
| E1 | Target role | **Cloud/DevOps engineer** | owner accepted default |
| E2 | Public visibility | **Not now; maybe later.** Keep everything private, and don't build the public case study yet | owner |
| E3 | IaC tool | **Terraform** | owner accepted default |

### Owner goal: employability (added 2026-10-14)

The owner wants this migration to strengthen their CV. That makes it a project goal alongside
"no functional change" and the $2 cap:

- **Step 4:** the route trade-off table gets a *portfolio value* column. Cost, risk and parity
  still decide first; portfolio value breaks ties.
- **Step 3:** the PRD records portfolio deliverables as owner requirements, not extras: a
  sanitised public case study, a synthetic demo dataset, measured cost and latency, and
  architecture decision records.
- **Never added just for the CV:** Kubernetes/EKS (about $73/month for the control plane alone),
  NAT gateways, load balancers, databases. None has a functional need, and each breaks the cap.
  The reasoning behind leaving them out is itself material for the case study.
- Target role: Cloud/DevOps engineer. IaC tool: Terraform. The public case study is deferred (E2).

### Feasibility notes from these answers (input to Steps 2 and 4)

- **AWS has no true hard cap.** Budgets refresh up to three times a day, and a budget action can
  only attach a deny policy or stop instances after the fact. "$2 hard cap" therefore has to be
  built into the design: concurrency limits, no always-on resources, and a budget action that
  stops the job's role.
- **Only pay-per-use designs fit $2.** That rules out NAT gateways, load balancers, always-on
  instances, Secrets Manager ($0.40 per secret) and customer-managed KMS keys.
- **OCR compute, first measurement (2026-10-14, Windows laptop, synthetic screenshots):** about
  11 s wall and 7 CPU-seconds per image, 740 MB peak memory, 3–5 s engine start-up. At 8,700
  images a month that fits inside Lambda's always-free 400,000 GB-seconds at 2 GB (≈ 196,000),
  or costs about $0.40 on Fargate Spot. The $2 cap looks achievable, but this must be re-measured
  on Linux in the target runtime. Details: `planning-artifacts/ocr-benchmark-2026-10-14.md`.
- **Running one job per upload costs about the same as a weekly batch.** The image count is the
  same; the only extra is model start-up (10–20 s per run). It needs a "folder upload finished"
  signal.
- **Unpinned versions change the OCR models (found 2026-10-14).** A fresh install of
  `requirements.txt` resolved to paddlepaddle 3.3.1, paddleocr 3.7.0 and paddlex 3.7.2, and the
  first run downloaded **`PP-OCRv6_medium_det`** as the default detector. The comments in
  `config.py:54-65` assume a PP-OCRv5 detector. The production PC's versions are unknown. To
  keep production's OCR quality, the AWS build must reproduce the production PC's exact package
  versions and model names. Needed: `pip freeze` and the list of
  `%USERPROFILE%\.paddlex\official_models` folders from that PC (neither contains private data).
- **≤ 2 h turnaround rules out one serial run over 2,000 images.** That takes about 2.8 h on the
  PC today (`docs/TECHNICAL.md:246-250`) and longer on a small container. Per-folder runs, or
  several parallel workers, meet it at the same cost.

## Constraints already fixed by the repo (binding on every step)

1. **No employee data in tracked files.** Nothing from `input_images/` or `output/`, no real names,
   spreadsheet IDs, phone numbers, Web App URL or secret. Examples use fictional names such as
   `Nguyen Van A & 2026-06-01` (`AGENTS.md` → Policy).
2. **Credentials live only in `secrets_local.py`; the name → spreadsheet map only in
   `apps_script/Employees.gs`.** Neither file is in the repo (`.gitignore:68`, `.gitignore:77`).
3. **Never run `main.py` or `resync_output.py` on real folders without the owner's go-ahead.**
   Both write to live employee Google Sheets (`AGENTS.md` → Running and verifying).
4. **The live Apps Script project is the source of truth.** Never paste local files over it, and
   redeploy only as a new version of the existing deployment (`AGENTS.md` → Known pitfalls,
   `apps_script/README_SETUP.md:81-89`).
5. **The OCR settings are load-bearing:** `paddle_enable_mkldnn = False`,
   `ocr_strict_engine = True`, no silent engine fallback, and `import runtime_env` first
   (`AGENTS.md` → Conventions, Known pitfalls).
6. **`post_9am_exception_limit` stays per day** (`AGENTS.md` → Known pitfalls).
7. **"No functional change"** means, for each folder, the same sessions, the same xlsx contents,
   the same Google Sheets writes, the same archive and retention behaviour, and the same failure
   semantics as `main.py --batch` today. The operator's input channel may need to change; that is
   an open question for the owner.

## Decision log

| # | Date | Decision | By | Reason |
|---|---|---|---|---|
| D-0 | 2026-10-15 | Use `_bmad-output/` (installed config) instead of `bmad-output/` | Winston | Installed config value; the BMAD skills read from it |
| D-1 | 2026-10-15 | Write the Step 1 inventory directly as Winston, not through `bmad-project-context` | Winston | `document-project` was retired in 6.12, and its replacement produces only the short AGENTS.md block, not a Phase 1–4 inventory |
| D-2 | 2026-10-14 | Record the owner's question-1 answers; defaults accepted for 1f, 1g, 1h, 1i, 1j | Owner | Answers to blocking question 1 |
| D-4 | 2026-10-14 | Add employability as a project goal; portfolio value breaks ties between routes | Owner | Owner wants the project to help their employability |
| D-5 | 2026-10-14 | All blocking questions settled: owner answers for 6 and E2, defaults for the rest | Owner | See the constraint rows 1b–E3 |
| D-6 | 2026-10-14 | Step 2 done: 18 adversarial + 16 edge-case + 7 verification-gap findings, grouped into 8 blockers, 6 security findings, 8 cloud-readiness smells (`planning-artifacts/risk-assessment.md`) | Winston | Lenses ran in sequence inside this session; no subagents were started |
| D-7 | 2026-10-14 | Step 3 PRD final (`planning-artifacts/prds/prd-calls-log-2026-10-14/prd.md`): FR-1 to FR-31, Fast path | Owner + John | Owner corrections: most folders have under 20 images, some 30–40, 40 at most; **both the PC and AWS can write to the Sheets, and the Owner chooses PC, AWS or both at any time** (replaces the single-writer rule); the PC stays an alternative with no end date |
| D-24 | 2026-10-15 | **Story 2.2 built; `main` is now PR-only with two required checks.** `ci.yml` (Privacy gate, which fails closed without the `PRIVATE_DENYLIST` secret, and Tests, image and OCR) replaced `runner-image.yml`. Actions are pinned to SHAs; Dependabot opened PRs #1/#2 for the v7 actions. Ruleset `25140628` blocks direct pushes, force-pushes and deletion and requires both checks (admin bypass only inside PRs); shown live on PR #3 ("the base branch policy prohibits the merge"). **Owner action pending:** export the denylist on the office PC and set it as the Actions and Dependabot secret; until then every PR stays blocked by design | Owner + Winston | Owner chose "PRs only" and "ruleset now"; spec `implementation-artifacts/spec-2-2-…` |
| D-23 | 2026-10-15 | **Story 2.1 done: the AWS account is ready to deploy from GitHub with no keys.** The Owner upgraded to Paid, set the $20 spend limit, activated advanced features and turned on Builder ID MFA. The bootstrap was applied from this PC after the Owner approved the plan: state bucket `calls-log-tfstate-333596351215` (versioned, BPA, SSE-S3, TLS-only, verified), GitHub OIDC provider, role `calls-log-deploy` trusting only `main` of `tannedjames/calls-log-aws` (verified word for word). Lambda concurrency is 400 already, so no quota request. No IAM user was needed: the Owner works through the Builder ID → `AccountFullAccessRole` via `aws login` | Owner + Winston | Spec `implementation-artifacts/spec-2-1-…` |
| D-22 | 2026-10-15 | **Region moved from ap-southeast-1 (Singapore) to ap-southeast-2 (Sydney).** The AWS project "Golden Hour Code" was created in Sydney, and its fixed region policy refuses other regions (apart from us-east-1/us-west-2 for global services). Cost is unchanged at this volume, and every service in the design exists in Sydney; turnaround is unaffected (batch uploads, 2 h target). Data is now stored in Australia. Updated: `infra/bootstrap` default, `docs/AWS_SETUP.md`, the PRD region row, NFR-7 in the epics, the spine (AD-18 and the stack table), and the Epic 2 context and spec. Historical analyses (risk assessment, solution-design cost tables) still say Singapore and are superseded by this entry | Owner | Owner chose "Switch to Sydney" over trying to move the project |
| D-21 | 2026-10-15 | **AWS account = the "new AWS experience" (project "Golden Hour Code", account 333596351215, in an AWS-managed org).** Its fixed SCPs deny `iam:*Provider*` (GitHub OIDC) and `iam:*LoginProfile*` (IAM user console passwords) on the Free and Paid plans alike, and a region floor allows only us-east-1, us-west-2 and the project's selected region. The console sign-in (root email) gives `aws login` sessions as `/managed/AccountFullAccessRole`. **Owner upgraded to the Paid plan** (pay-as-you-go, credits used first). The project spend limit can't go below **$20**, so it's the last-resort hard cap; the $2 target stays enforced by the Story 2.4 budget alerts and the Epic 3 guardrails. Next: activate advanced features (free, same account) so OIDC works, and confirm the project region is ap-southeast-1. Story 2.1 is paused until then | Owner | AWS docs "Managed policies for your organization" (projects), "Upgrade your account", aws.amazon.com/start-here |
| D-20 | 2026-10-15 | **Story 1.6 done; Epic 1 complete.** `tools/parity_check.py` ran the locally built runner image (`calls-log-runner:local`, Docker Desktop on WSL 2) on the three reference folders: **all IDENTICAL** (F1 3 Sessions, F2 5, F3 the 6 rule-based ones). The folders were mounted read-only and stayed unchanged; output was by label only. A deliberately wrong expectation was reported as DIFFERENT with exit 1. The private expectations file is `C:\Users\admin\Downloads\calls-log\parity-expected.json` (outside the repo, gitignored by name). FR-26 is met on this machine's package set | Winston | Spec `implementation-artifacts/spec-1-6-…` |
| D-19 | 2026-10-15 | **Story 1.5 done: the Linux runner image builds and passes CI** (run 38679207686 on `f80e7dc`). Lambda `python:3.13` base pinned by digest; `requirements.lock` with hashes; `mesa-libGL`, `glib2` and **`libgomp`** (found missing by CI run 1); 11 model files verified against `models.sha256` and baked read-only. The CI proves offline, read-only, non-root OCR on the Synthetic Set, `--cpus 1` gives 1 thread, and decoy private files stay out. **Size: 2,205 MB uncompressed, 806 MB compressed.** GitHub CLI signed in as tannedjames; Docker Desktop 29.8.2 running on WSL 2 (`hello-world` OK; 16 CPUs, 7.6 GB, cgroup v2) | Winston | Spec `implementation-artifacts/spec-1-5-…`; Owner approved the push at the spec checkpoint |
| D-18 | 2026-10-14 | **Git set up and first push.** Private repo `https://github.com/tannedjames/calls-log-aws` (404 without sign-in); branch `main`; first commit `cde5e356ec43672dec82bf64a89fe65808117279` (290 files) is on `origin/main`. Author `James <tancoolk@gmail.com>`; `core.hooksPath .githooks` turned on (the privacy check ran at commit and push: `OK`, file types only). Before pushing, a scan for the reference-folder names, JP/VN phone numbers and Google IDs/URLs found nothing. The graphify hooks moved from `.claude/settings.json` to the gitignored `.claude/settings.local.json`. Part of Story 2.1 (private repo) is done | Owner | Owner gave the repo URL and chose "push to main", this author and "keep hooks local" |
| D-17 | 2026-10-14 | **Story 1.4 done.** `tools/gen_synth.py` + `tools/check_synth.py`: fictional screenshots, photos and sideways photos with ground truth; all calls match on two seeds; 97 tests. **This machine has no `secrets_local.py` or `Employees.gs`**, so the privacy check (run through a temporary git index) could verify file types only, not secret or name values; the full check must run on the office PC before any push | Winston | Spec `implementation-artifacts/spec-1-4-…` |
| D-16 | 2026-10-14 | **Stories 1.2 and 1.1 approved after the walkthrough; Story 1.3 done.** 9 characterisation tests (folder commit rules, Day Sheet layout, load-bearing settings) bring the suite to 94; each of 7 deliberate behaviour changes fails a test. No product code changed | Owner (approval) + Winston | Owner: "next part of implementation" after the walkthrough; spec `implementation-artifacts/spec-1-3-…` |
| D-15 | 2026-10-14 | **Stories 1.2 and 1.1 done.** 1.2: orientation classification only for landscape images; the Reference Set run with the real code matched all three folders. 1.1: `requirements.lock` (hashed, universal, Python 3.13) pins this machine's set; a clean venv reproduces it exactly. `requirements.txt` now pins `opencv-python==4.10.0.84`, **which replaces D-3's external constraints file**. The office PC gets both by copying the code and building a fresh venv from the lock | Winston | Specs in `implementation-artifacts/spec-1-2-…` and `spec-1-1-…` |
| D-14 | 2026-10-14 | **Implementation started (overrides D-13's stop).** First story: 1.2 (orientation fix). **OQ-1 resolved: the lockfile pins this machine's 2026-10-14 package set** (paddleocr 3.7.0, paddlex 3.7.2, paddlepaddle 3.3.1); the office PC reinstalls from `requirements.lock`. Story reviews run inline, without subagents | Owner | Owner: "continue with implementation"; this set reproduces all three references with the fix (D-12). Docker and WSL aren't installed here, so 1.5 and 1.6 can be written but not verified on this machine |
| D-13 | 2026-10-14 | **Step 5 complete; flow stops here.** `planning-artifacts/epics.md`: 6 epics, 28 stories (17 S, 11 M), all 32 FRs and 8 NFRs covered. Phase 7: containerise (E1), move state out of local folders (E3), secrets (E2, E3, E5), IaC and CI/CD with OIDC → ECR (E2), DB migration, DNS/TLS cutover and rollback (E5). No implementation started | Owner + John | Owner's brownfield flow: "Stop after step 5. Do not start dev/implementation stories." |
| D-12 | 2026-10-14 | **All three references are identical with the orientation fix simulated** (classifier off; all images here are portrait). Folders 1 and 2: unchanged sessions and record counts (101/101, 102/102). Folder 3: 6 rule-based sessions, +7 calls recovered from the photo. Side effect: 7.6–12 s per image instead of 19–32 s on this laptop | Winston | Read-only scratch run on this machine's install (paddleocr 3.7.0 / paddlex 3.7.2 / paddlepaddle 3.3.1); not yet the production PC's versions (OQ-1) |
| D-11 | 2026-10-14 | **Folder 3 root causes and the owner's ruling.** (1) Image 3 is a phone **photo**; PaddleOCR's page-orientation classifier wrongly rotated it 90°, so 0 calls were read (with the classifier off, all 7 calls at 08:33–08:47 are read). Fix: run orientation classification only on landscape images. This is an accuracy fix to the shared core, applied to the PC and AWS alike (PRD FR-32). (2) The 14:17 → 14:23 merge in the hand-corrected sheet needs a 5th post-09:00 exception. **Owner chose to keep the rules (option 2)**, so folder 3's parity expectation is the rule-based result: 08:01–09:57, 11:22–12:01, 12:36–13:48, 13:54–14:17, 14:23–14:23, 14:30–15:01 | Owner | Owner answer "2"; diagnosis from the row trace and the orientation on/off test |
| D-10 | 2026-10-14 | **Parity target = all three references.** Folders 1–2 are unedited PC output; folder 3 was hand-corrected because the PC's earlier output was wrong. Owner: "it's best if your result is identical to all three." First run on this machine's install: folders 1–2 identical; folder 3 splits two sessions (an 18-min gap before 09:00, and a 6-min gap after 09:00 with all 4 daily exceptions used). If closing folder 3 needs a change to OCR or parsing, that is an accuracy fix to the shared core (applies to the PC too, AD-1), not part of "no functional change", and is tracked as its own story | Owner | Owner statement 2026-10-14; root-cause trace running |
| D-9 | 2026-10-14 | **Reference Set received (OQ-2):** 3 Person-Day folders (15, 15 and 16 JPG screenshots), each with a `correct output.png` image of the expected session rows. Stored outside the repo as sibling folders of `calls-log-main`; folder names are real employee names and are deliberately not recorded here. One reference shows cleared rows (0:00), so "correct" may be human-corrected rather than raw PC output | Owner | Owner supplied it for FR-26; first comparison is run read-only against this machine's install |
| D-8 | 2026-10-14 | **AWS route: B, Balanced (serverless queue).** Upload page (Lambda Function URL) → S3 → SQS → Lambda container runner running the unchanged pipeline, DynamoDB Folder Status, guardrails in infrastructure, Terraform + GitHub OIDC. **Fallback:** the same runner image on Fargate Spot (Route A compute) if Phase 0 measures Lambda GB-s over budget. Rejected: Route A as primary (misses FR-2, FR-3, FR-6, FR-15, weak FR-18; $1.15 expected), Route C (per-image fan-out adds parity and cost risk, $1.01 expected / $2.20 pessimistic, runs against SM-C3) | Winston; Owner may override before Step 5 | Only route covering every FR with headroom: ≈ $0.39/month expected, OCR inside Lambda's always-free 400k GB-s; low parity risk (same functions, same order); native testable guardrails (FR-18 exposure ≈ $0.06); strongest honest portfolio piece. Details: `planning-artifacts/architecture/architecture-calls-log-2026-10-14/solution-design.md` §6 |
| D-3 | 2026-10-14 | Install with `opencv-python==4.10.0.84` pinned through a constraints file kept outside the repo | Owner | `paddlex` pins `opencv-contrib-python==4.10.0.84`. Unpinned, `opencv-python` would resolve to 5.x and both would write `cv2` |
