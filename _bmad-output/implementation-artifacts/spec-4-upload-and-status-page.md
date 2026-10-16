---
title: 'Epic 4 (Stories 4.1-4.4): the upload and status page'
type: 'feature'
created: '2026-10-16'
status: 'in-progress'
route: 'dispatch'
review_loop_iteration: 0
baseline_commit: 'claude/story-3-8-reprocess'
context: ['{project-root}/_bmad-output/planning-artifacts/architecture/architecture-calls-log-2026-10-14/ARCHITECTURE-SPINE.md']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Folders reach AWS only through `tools/enqueue.py`, which needs the Owner's PC, Python and `aws login`. The Operator has no way in, and nobody can see a Folder's status or fetch a Day Sheet without the AWS console.

**Approach:** One small Lambda, `calls-log-intake`, behind a public Function URL (AD-17), serves one page and a passphrase-protected JSON API:
- pick a folder, or a parent folder of day folders, in the browser;
- the page and the server check every folder (name, registered employee, images);
- the server issues one presigned POST per image, and the browser uploads straight to S3;
- the Operator clicks Ready, which checks the count, marks the folder Pending and queues it;
- a status view for the last 7 days, refreshing every minute;
- a Day Sheet search by employee and date that downloads through a 5-minute presigned link;
- the AWS Sync Switch, shown read-only.

**Decisions** (the Owner's standing instruction, D-28: work uninterrupted; recorded here):
- **Passphrase** (AD-17): `/calls-log/intake_passphrase_hash` holds `pbkdf2_sha256$<iterations>$<salt>$<hash>`, 600,000 iterations (OWASP 2023). `cloud/passphrase.py` hashes and verifies; `tools/put_parameters.py --passphrase` prompts twice (12 characters at least) and stores only the hash. The page sends the passphrase in a header on every API call and keeps it in memory only. The server checks it before touching S3, DynamoDB or SQS. A wrong one costs a full PBKDF2 and gets 401. A right one is remembered per container as a SHA-256 digest, compared with `hmac.compare_digest`, so only the first call in a container pays the PBKDF2.
- **Uploads** (FR-1 to FR-3): every presigned POST names its exact key `inbox/<uploadId>/<file>`, its `Content-Type` from the suffix, 1 byte to 15 MB, 15 minutes. So nothing outside the declared files can land in an upload. Ready lists `inbox/<uploadId>/` and needs exactly the declared count. Ready on a Pending folder re-sends its message: a failed send can be retried, and the runner skips a duplicate (`Skipped`). Uploading into a Ready folder has no API; a correction is a new upload, which re-processes the Person-Day.
- **What the server refuses** (FR-2), in plain words: a name that isn't `<Name> & <yyyy-mm-dd>` with a real date; a name that matches no `name_prefixes` entry, or no list at all; no supported image; only HEIC; more than `max_images` images; an image over 15 MB; the same Person-Day twice in one batch. Files of other types are ignored and named, as the PC ignores them. Files in sub-folders of a day folder are ignored, as on the PC.
- **Status view** (FR-15): `status.create` also writes `upDay` (the uploadId's UTC date). A global index, `uploads-by-day` (`upDay`, `sk`, all attributes), lets the page query 8 UTC days with no scan: about 20 items a day. The page shows the newest upload of each Person-Day, with its status, last Run (local time) and last error. It refreshes every 60 s while the tab is visible.
- **Day Sheets** (FR-11): a search lists `results/` and matches the name with the Web App's fold (`naming.norm`) and the date exactly. It returns at most 50 sheets, newest date first, each with a 5-minute presigned GET that downloads `<Person-Day>.xlsx`.
- **Cost guardrail** (AD-11): reserved concurrency 2, and two alarms that make the kill switch set it to 0 and email: `calls-log-intake-runaway` at 600 invocations in 15 minutes (normal use is under 100), and `calls-log-intake-busy` at 60 s of compute in 15 minutes (normal use is under 20 s). A wrong passphrase costs a PBKDF2 of about 1.4 s, so the second catches a slow guesser who stays under 600 requests: about 40 tries a quarter-hour at most, and about 86,000 GB-s a month at most below both alarms. Terraform ignores that setting after creation, so a deploy doesn't undo it. Turning the page back on is a runbook step.
- **Page security:** one HTML file with inline CSS and JS. Its CSP allows only those exact blocks (by SHA-256), the API itself and the bucket's regional endpoint. No frames, no referrer, and `no-store` on the API.
- **Bootstrap** (applied by hand before merge): the deploy role gets `s3:PutBucketCORS` on the data bucket; the boundary and the kill switch get `lambda:PutFunctionConcurrency` on `calls-log-intake` only.

## Boundaries & Constraints

**Always:** `cloud/status.py` stays the table's only writer and `cloud/naming.py` the only name parser; the passphrase is checked before any data call; secrets, the passphrase and names never reach a log line; the role sits under `/calls-log/` with the boundary.

**Never:** AWS credentials in the browser beyond a presigned form; a wildcard key in a presigned POST; CORS for any origin but the Function URL; a deploy that undoes the kill switch; a scan of the status table; the page reading or changing the Sync Switch.

</frozen-after-approval>

## Code Map

- `cloud/passphrase.py`, `cloud/intake.py`, `cloud/static/upload.html`.
- `cloud/status.py` (`upDay`, `recent`), `cloud/settings.py` (`intake_function`), `cloud/killswitch.py` (the intake alarm throttles the intake).
- `tools/put_parameters.py` (`--passphrase`).
- `infra/intake.tf`, `infra/status.tf` (index), `infra/guardrails.tf` (alarm, kill-switch right), `infra/data.tf` (CORS), `infra/outputs.tf` (`intake_url`); `infra/tests/intake.tftest.hcl`.
- `infra/bootstrap/main.tf`: `s3:PutBucketCORS`; boundary `ThrottleTheUploadPage`.
- `docs/TECHNICAL.md` (code map, Runbook: the page, turning it back on), `docs/AWS_SETUP.md` (the bootstrap re-apply, the passphrase).

## Tasks & Acceptance

**Execution:**
- [ ] `cloud/passphrase.py` and `put_parameters.py --passphrase`.
- [ ] `cloud/intake.py`: routing, passphrase gate, check, uploads, ready, status, results; tests.
- [ ] `cloud/static/upload.html`.
- [ ] `status.py` `upDay` and `recent`; kill switch dispatch; settings.
- [ ] Terraform: intake function, URL, both permissions, CORS, index, alarm; bootstrap; tests.
- [ ] Docs and runbook.
- [ ] **Owner:** bootstrap plan and apply (CloudShell), then merge after #17.
- [ ] **Owner:** `put_parameters.py --passphrase`; open the page; the live checks below.
- [ ] **Live matrix** (Synthetic Set only): a good folder uploads, goes Ready and reaches Done; a bad name, 41 images and HEIC-only are refused before upload; an interrupted upload can't be made Ready; a wrong passphrase gets 401; a Day Sheet downloads; a forced intake alarm turns the page off and the runbook turns it back on.

**Acceptance Criteria:** epics.md Stories 4.1-4.4.

## Verification

**Commands:**
- `terraform fmt -check -recursive infra`; `init -backend=false`, `validate` and `test` in `infra/` and `infra/bootstrap/`.
- `python -X utf8 run_tests.py`.
- `python -X utf8 tools/check_private_data.py`: OK.
