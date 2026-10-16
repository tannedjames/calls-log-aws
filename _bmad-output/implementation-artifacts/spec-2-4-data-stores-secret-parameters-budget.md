---
title: 'Story 2.4: Data stores, secret parameters and the $2 budget'
type: 'feature'
created: '2026-10-15'
status: 'done'
route: 'dispatch'
review_loop_iteration: 0
baseline_commit: '1b05081e272d67006f13b41d4bb647d75f6835ee'
context: ['{project-root}/_bmad-output/implementation-artifacts/epic-2-context.md']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Epic 3 needs somewhere to keep uploads, checkpoints and Day Sheets, the Web App URL and secret at run time, and a warning long before the $2 cap. None exists: the account has no data bucket, no parameters, no budget, and no account-level Block Public Access.

**Approach:** Add the data bucket, the six SSM parameters, an alert topic and the $2 budget to `infra/`, deployed by the pipeline. Widen the deploy role in the bootstrap (hand-applied before the merge). Add a PC tool that sets the real parameter values out of band.

**Decisions** (the Owner said to work uninterrupted; recorded here and as D-28):
- **Bucket `calls-log-data-<account>`:** Block Public Access, Object Ownership `BucketOwnerEnforced` (ACLs off), SSE-S3, versioning, `prevent_destroy`, and a policy denying non-TLS. Lifecycle:
  - `inbox/` and `work/`: current versions expire after 30 days, noncurrent versions 1 day later;
  - `results/`: no rule, so every version is kept;
  - bucket-wide: incomplete multipart uploads aborted after 1 day, expired delete markers removed.
- **Account-level Block Public Access goes in the bootstrap,** not `infra/`, so the deploy role can never turn it off.
- **Parameters** `/calls-log/{webapp_url, sync_secret, sync_enabled, name_prefixes, intake_passphrase_hash, alert_email}`: all SecureString, written once through the write-only `value_wo` (version 1), with placeholder `unset` (`sync_enabled`: `false`). Provider 6.69 stores `value = null` in state when `value_wo` is used, and never rewrites the parameter until `value_wo_version` changes. This replaces `ignore_changes = [value]`, which would still copy the real value into state on refresh.
- **PR plans run with `-refresh=false`.** A refresh reads every parameter with decryption, so a refreshing PR plan would need `ssm:GetParameter`, and any PR could then read the secret. The plan role keeps exactly its current read-only rights. The deploy job still refreshes, and its drift check is unchanged.
- **Alerts:** SNS topic `calls-log-alerts` (unencrypted: Budgets can't publish to an `aws/sns`-encrypted topic, and messages carry no PII), whose policy lets `budgets.amazonaws.com` from this account publish. Budget `calls-log-monthly`: $2.00 per month, cost, with 6 notifications to the topic: ACTUAL and FORECASTED at 50, 80 and 100%. Budgets allows up to 10.
- **The alert email is never in the repo, state or plan comments.** `tools/put_parameters.py --alert-email` stores it in `/calls-log/alert_email` and subscribes it to the topic; the Owner confirms the AWS email.
- **`tools/put_parameters.py`** (AWS CLI, standard library only): `--from-private-files` reads `secrets_local.py` and the `NAME_TO_ID` keys of `apps_script/Employees.gs`, in file order, as a JSON array. `--sync-enabled true|false`. It writes through a temporary file, never argv or the terminal, and prints only parameter names.
- **Deploy role** (bootstrap):
  - the data bucket's configuration, with no object access and no `DeleteBucket`;
  - parameters under `/calls-log/*`, plus `ssm:DescribeParameters`;
  - topics `calls-log-*`;
  - budgets `calls-log-*`.
- **Cost:**
  - S3 about $0.10 a month (about 2.6 GB of 30-day images at $0.025 per GB, plus about 9k PUTs);
  - SSM standard parameters, the `aws/ssm` key, SNS email (1,000 free) and one budget: $0.

## Boundaries & Constraints

**Always:** `ap-southeast-2`; the bootstrap is applied after its plan is read, before the PR merges; run `check_private_data.py` before every commit and push; values never printed or logged.

**Never:** a real value, an email or an employee name in the repo, state, plan output or PR comments; `ssm:GetParameter`, write or data access for the plan role; object access or `s3:DeleteBucket` for the deploy role; `terraform apply` of `infra/` from the PC.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| First deploy | parameters absent | created with placeholders; state `value` null | AccessDenied → fix the bootstrap, re-run |
| Real value set out of band | `put_parameters.py` | the next deploy changes nothing; state still has no value | N/A |
| Public or non-TLS request | anonymous GET, `http://` | denied | N/A |
| Private files missing | `--from-private-files` on a PC without them | exit 2, names the missing file, writes nothing | N/A |
| `aws` CLI fails | expired `aws login` | exit 1, the CLI's error, no value shown | N/A |

</frozen-after-approval>

## Code Map

- `infra/ecr.tf` -- pattern for header comments with cost lines; new files sit beside it (`data.tf`, `parameters.tf`, `alerts.tf`). `infra/variables.tf` has `account_id`/`region`.
- `infra/tests/ecr.tftest.hcl` -- mock-provider pattern for new `infra/tests/*.tftest.hcl`.
- `infra/bootstrap/main.tf:20-22` locals (`ecr_repositories` pattern), `:192-239` `deploy_ecr` pattern for the new policy documents; `:258` `plan_read` stays unchanged.
- `infra/bootstrap/tests/ci_roles.tftest.hcl` -- extend: the plan role's action set is unchanged, deploy S3 rights stay on the data bucket's ARN with no object ARN and no `DeleteBucket`.
- `.github/workflows/deploy.yml` -- the `Plan` step's `terraform plan` gets `-refresh=false`, and the comment says so.
- `tools/check_private_data.py:59-87` `private_values()` -- the same regexes for reading the private files; reuse its approach, not the module (it is a CLI).
- `docs/AWS_SETUP.md` §3 (import list, account BPA), §5 (Budget email, Secret values → the tool); `docs/TECHNICAL.md` §10 (PR plans don't refresh).
- `_bmad-output/project-context.md` -- D-28.

## Tasks & Acceptance

**Execution:**
- [x] `infra/data.tf`, `infra/parameters.tf`, `infra/alerts.tf`, `infra/outputs.tf` -- the resources above -- outputs bucket name, topic ARN.
- [x] `infra/tests/data.tftest.hcl`, `infra/tests/parameters_alerts.tftest.hcl` -- assert the bucket baseline, the lifecycle prefixes, the deny-non-TLS statement, 6 SecureString parameters with no `value`, and the budget limit and 6 notifications.
- [x] `infra/bootstrap/main.tf`, `tests/ci_roles.tftest.hcl` -- account BPA, deploy policies `data-bucket`, `parameters`, `alerts`; tests.
- [x] `.github/workflows/deploy.yml` -- `-refresh=false` on PR plans.
- [x] `tools/put_parameters.py`, `tests/test_put_parameters.py` (or `run_tests.py` cases) -- the tool, with the AWS CLI faked.
- [x] Docs and D-28.
- [ ] Bootstrap plan → apply; PR → merge → deploy; set real values; verify.

**Acceptance Criteria:**
- Given `infra/`, when `fmt`, `validate` and `test` run offline, then all pass.
- Given the deploy, when the bucket is inspected, then Block Public Access (account and bucket), SSE-S3, versioning, `BucketOwnerEnforced`, the TLS-deny policy and the lifecycle rules match the decisions.
- Given the real values set with the tool, when `terraform state pull` is searched for them, then none is found, and a fresh deploy plans no changes.
- Given an anonymous HTTPS GET of an object URL and a plain-HTTP request, when sent, then both are refused.
- Given the budget, when described, then it is $2.00 monthly with the 6 notifications on the topic.

## Implementation Notes

- 2026-10-15, built inline (no subagents, the Owner's standing choice). The Owner said to work uninterrupted, so checkpoint 1 was passed on judgment; the spec is about 1,900 tokens and kept whole, because the bucket, parameters, budget and the deploy rights for them ship together.
- **Files:** `infra/data.tf`, `parameters.tf`, `alerts.tf`, `outputs.tf`, `tests/stores.tftest.hcl` (new, real provider offline), `tests/ecr.tftest.hcl` (the mock provider now returns valid JSON for policy documents); `infra/bootstrap/main.tf` (account BPA; policies `data-bucket`, `parameters`, `alerts`) and its tests; `deploy.yml`; `tools/put_parameters.py` with 6 tests in `tests.py`; `docs/AWS_SETUP.md`, `docs/TECHNICAL.md`; D-28.
- **Small departures from the spec text:**
  - the test file is `infra/tests/stores.tftest.hcl` (one file for the three areas), not two files;
  - the topic ARN is written out in a local (`alerts_topic_arn`), as the bootstrap does, so the topic policy can be checked at plan time.
- **Provider calls, checked in the v6.69.0 source** before writing the deploy rights: `aws_s3_bucket` reads through HeadBucket (`s3:ListBucket`) and 13 Get calls; tag-on-create needs `s3:TagResource`, `sns:TagResource` and `budgets:TagResource`. Budget `limit_amount` has a diff-suppress for equal amounts. `aws_ssm_parameter` with `value_wo` sets `value = nil` on every read (`parameter.go:331-349`) but still reads with decryption, hence `-refresh=false` on PR plans.
- **Verified locally** (Terraform 1.16.5, provider 6.69.0): `fmt` clean; `validate` both modules; `terraform test`: infra 6 passed, bootstrap 6 passed; `run_tests.py` 134 passed. 13 mutations each fail the right run: noncurrent 30 days, TLS deny inverted, ACLs on, whole-bucket expiry, results expiring, inbox 60 days, sync on by default, an email in Terraform, deploy `s3:GetObject`, deploy `ssm:DeleteParameter`, account BPA weakened (the other two are covered by 2.3's checks).
- **Terraform 1.16.5 panics** ("value has marks") while printing a failed assertion over the whole lifecycle `rule` list, as in Story 2.3. The lifecycle assertions use explicit indexes and the module's local instead.
- **Bootstrap plan** (against the real local state, `-state` pointing at the main checkout's file, backed up first): 4 to add (account BPA and the three policies), 0 to change, 0 to destroy. **The apply was blocked by Claude Code's auto-mode classifier** ("Protected-Scope IaC Apply"): the hand-applied bootstrap is the Owner's gate (D-26). Waiting for the Owner to apply it before the merge.

## Spec Change Log

## Review Triage Log

Pass 1, 2026-10-15. Three lenses run inline: blind hunter (diff 50 kB, floor 8), edge-case hunter with the claims check, and verification gap.

- **low (patched):** running `--alert-email` with a new address leaves the old one subscribed. AWS_SETUP now says to unsubscribe the old one from any alert email.
- **low (patched):** the Owner couldn't check that every NAME_TO_ID key reached `name_prefixes` without printing names. The tool now prints the count only; a test pins it.
- **low (rejected):** a missing `aws` executable gives a traceback (no value is printed). The CLI is a documented prerequisite, and handling it adds a guard.
- **low (rejected):** `ssm:GetParameters` is granted but the provider uses `GetParameter`. No harm: the role can read the same parameters either way.
- **false:** "overlapping lifecycle filters are rejected." The S3 guide (lifecycle-conflicts, Example 3) allows overlapping filters, even with conflicting actions.
- **false:** "`limit_amount = 2.0` drifts forever." The provider suppresses equivalent amounts (`budget.go:207`).
- **false:** "PR plans hide console drift." By design (D-28); the deploy refreshes and fails on drift after the apply.
- **false:** "double-quoted NAME_TO_ID keys are missed." All 17 keys in the real file are single-quoted, the same assumption `check_private_data.py` makes.
- **verification gap (not a finding):** removing `-refresh=false` makes the PR plan fail loudly with AccessDenied, because the plan role has no SSM access.
- **verification gap (live only):** whether the deploy rights are enough can only be shown by the first deploy (matrix row 1); the bootstrap tests pin their scope.
- **verification gap (patched):** Terraform 1.16.5 panicked instead of failing cleanly on two lifecycle mutations; the assertions were rewritten and all 13 mutations now fail cleanly.

## Verification

**Commands:**
- `terraform -chdir=infra fmt -check -recursive`; `init -backend=false`; `validate`; `test` (both modules) -- all pass.
- `.venv\Scripts\python.exe -X utf8 run_tests.py`; `tools\check_private_data.py` -- OK.
- `aws s3api get-bucket-*`, `aws s3control get-public-access-block`, `aws ssm describe-parameters`, `aws budgets describe-notifications-for-budget` -- as in the ACs.
