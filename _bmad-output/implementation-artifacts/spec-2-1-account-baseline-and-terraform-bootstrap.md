---
title: 'Story 2.1: Account baseline, private repo and Terraform bootstrap'
type: 'feature'
created: '2026-10-15'
status: 'done'
route: 'dispatch'
review_loop_iteration: 0
baseline_commit: '4f04f6397874fff0bddb630dd7e193bf3423bc26'
context: ['{project-root}/_bmad-output/implementation-artifacts/epic-2-context.md']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The new AWS account has only its root user. There's nowhere to keep Terraform state, and GitHub can't reach AWS without stored keys, which the design forbids.

**Approach:**
- **Bootstrap code:** `infra/bootstrap/`, applied by hand once from this PC after `aws login`. It creates the encrypted, versioned state bucket, the GitHub OIDC provider and the deploy role `calls-log-deploy`, which trusts only `main` of `tannedjames/calls-log-aws`.
- **Runbook:** `docs/AWS_SETUP.md` lists every manual step in one place.
- **Quota:** the Lambda concurrency increase is requested once the Owner says yes.

**Decisions:**
- **State bucket** `calls-log-tfstate-<account id>` in `ap-southeast-2` (Sydney; the Owner moved the region on 2026-10-15, D-22):
  - versioning; all four Block Public Access settings; SSE-S3; Object Ownership `BucketOwnerEnforced`;
  - a bucket policy denying any request without TLS;
  - noncurrent versions expire after 90 days; `prevent_destroy`.
- **OIDC provider:** `token.actions.githubusercontent.com`, audience `sts.amazonaws.com`.
- **Deploy role trust:** `StringEquals` on both `aud = sts.amazonaws.com` and `sub = repo:tannedjames/calls-log-aws:ref:refs/heads/main`. No wildcards; sessions last at most 1 hour.
- **Deploy role permissions now:** only the state bucket (list the bucket; read, write and delete objects under `prod/`, which includes the lock file). Story 2.3 adds what `infra/` needs.
- **Bootstrap state:** the bootstrap's own state stays local and gitignored (`*.tfstate*`, `.terraform/`); `.terraform.lock.hcl` is committed. If it's lost, re-import with the commands in the runbook.
- **Versions:** Terraform `>= 1.10`, `hashicorp/aws ~> 6.56`; default tags `project=calls-log`, `env=prod`, `managed-by=terraform`.
- **Quota request:** Lambda concurrent executions raised to 1,000, so that reserved concurrency for the runner and intake can be set later (AD-11). It's filed through the CLI only after the Owner approves it.
- **Cost:** the state is under 1 MB of S3 (about $0.00 a month); IAM and OIDC are free.
- **Who does what:** the Owner does the console steps (Paid plan, root MFA, an admin IAM user with MFA). `terraform apply` runs only after the Owner has seen the plan and approved it.
- **Done when:**
  - `terraform fmt -check` and `validate` pass;
  - the apply is done, and the AWS CLI shows the bucket settings, the provider and the exact trust policy;
  - the quota is requested and `aws lambda get-account-settings` is recorded;
  - the runbook is written, and the tests and privacy check pass.

## Boundaries & Constraints

**Always:** `ap-southeast-2`; short-term CLI credentials (`aws login`) only; every resource tagged.

**Never:** no access keys for any identity; no wildcard or repo-wide trust; no resource with a fixed monthly fee; no root user for CLI work once the admin user exists.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Deploy from main | GitHub token `sub = repo:tannedjames/calls-log-aws:ref:refs/heads/main` | Assume role succeeds | N/A |
| Other branch, PR or fork | `sub` = another ref, `:pull_request` or another repo | STS refuses | Trust policy checked here; live refusal proven in Story 2.3 |
| Plain-HTTP request to the state bucket | `aws:SecureTransport = false` | 403 | Bucket policy denies |
| Public grant attempt | Public ACL or policy | Rejected | Block Public Access |
| Lost bootstrap state | `terraform.tfstate` deleted | Resources untouched | Re-import (runbook) |

</frozen-after-approval>

## Implementation Notes

- 2026-10-15, resolved with the Owner:
  - **Option (a) taken:** the Owner upgraded to the Paid plan and activated advanced features.
  - `iam simulate-principal-policy` for the Owner's role, including organization policies, now allows `iam:CreateOpenIDConnectProvider`, `iam:CreateRole`, `iam:PutRolePolicy` and `s3:CreateBucket`.
  - The region floor remains: ap-southeast-1 is denied by an SCP, ap-southeast-2 works, so the design moved to Sydney (D-22).
  - The Owner works through a non-root identity: an AWS Builder ID sign-in that gets `AccountFullAccessRole` sessions through `aws login`. **No IAM user is needed**; that task is replaced by Builder ID MFA (Owner).
  - **Lambda concurrency is already 400** (`get-account-settings`), enough for reserved concurrency of 3 + 2 with 395 left unreserved, so **no quota request is needed**. That task is closed with this evidence.
  - The project spend limit is set to $20, the lowest AWS allows.

- 2026-10-15: `aws login` (Owner, root email) returned `assumed-role/AccountFullAccessRole/<id>`, a role at `/managed/` trusted by `account-access.amazonaws.com`. The session couldn't refresh (`signin:CreateOAuth2Token` missing). `CreateLoginProfile` from CloudShell was denied by an SCP. AWS's docs ("Managed policies for your organization" for projects) list the SCPs above. Implementation is paused here (bootstrap code written, nothing applied) until the Owner chooses.

- Applied 2026-10-15 (Terraform 1.16.5, AWS provider 6.69.0, `fmt` and `validate` clean). The Owner approved the plan: 10 to add, 0 to change, 0 to destroy. Outputs: state bucket `calls-log-tfstate-333596351215`, deploy role `arn:aws:iam::333596351215:role/calls-log-deploy`, OIDC provider `arn:aws:iam::333596351215:oidc-provider/token.actions.githubusercontent.com`.
- Verified with the AWS CLI:
  - versioning `Enabled`; Block Public Access all four `True`; `AES256`; `BucketOwnerEnforced`; lifecycle `Enabled`, 90 days; the bucket policy is exactly `DenyNonTLS` on `aws:SecureTransport=false`.
  - The trust policy is exactly `StringEquals` on `sub = repo:tannedjames/calls-log-aws:ref:refs/heads/main` and `aud = sts.amazonaws.com`.
  - A plain-HTTP `ListObjectsV2` was **denied** ("explicit deny in a resource-based policy"), and `put-bucket-acl public-read` was **rejected** by BlockPublicAcls.
- Owner baseline:
  - Paid plan; advanced features activated; MFA on the Builder ID sign-in.
  - Non-root identity (an `AccountFullAccessRole` session via `aws login`); no access keys anywhere.
  - Lambda concurrency limit 400 (no request needed).
- The bootstrap state stays local at `infra/bootstrap/terraform.tfstate` (gitignored; ARNs only, no secrets); the re-import steps are in `docs/AWS_SETUP.md`. `.terraform.lock.hcl` is committed.

## Review Triage Log

Inline review, at the Owner's choice.

- maybe-false (deferred to Story 2.3): the GitHub token's `sub` format is assumed to be the default `repo:<owner>/<repo>:ref:refs/heads/main`. If the repo or org ever customizes the subject claim (for example to ID-based claims), the role would refuse `main` too. To settle it, run 2.3's first deploy on `main` (it must succeed) and a branch run (it must be refused).
- low (accepted): the bootstrap state lives only on this PC. If it's lost, AWS is untouched and re-importing takes ten documented commands; a remote state for the bootstrap would need a second bucket, which isn't worth it here.
- false: "the TLS-deny policy could lock Terraform out." Terraform and the CLI always use HTTPS. Only the deliberate plain-HTTP test was refused, and normal reads worked.
- false: "the account's region policy will block the OIDC provider." IAM is global and allowed in us-east-1 by the region policy; the apply created it without error.

## Code Map

- `infra/bootstrap/` (new): `versions.tf` (pins, provider, default tags), `main.tf` (bucket + controls, OIDC provider, role + state-bucket policy), `variables.tf` (`github_repo`, `region`), `outputs.tf` (bucket name, role ARN).
- `docs/AWS_SETUP.md` (new): the one-place manual checklist and the bootstrap commands.
- `.gitignore`: Terraform state, plans and `.terraform/`.
- `AGENTS.md`/`README.md`: unchanged (agent rules aren't edited by a story); README links the runbook.

## Tasks & Acceptance

**Execution:**
- [x] `infra/bootstrap/*.tf` -- resources as decided -- AC bucket, OIDC, role.
- [x] `.gitignore` -- `*.tfstate`, `*.tfstate.*`, `.terraform/`, `*.tfplan`.
- [x] `docs/AWS_SETUP.md` + a README link -- the manual steps in one place.
- [x] Owner console steps confirmed; `aws login` as the admin user; `sts get-caller-identity` shows a non-root ARN.
- [x] `terraform init/plan` -- Owner approves the plan -- `apply`; record the outputs.
- [x] Verify with the AWS CLI: bucket versioning, BPA, encryption, ownership, policy; the role's trust policy, word for word.
- [x] Quota request after the Owner's OK; record `get-account-settings`.

**Acceptance Criteria:**
- Given the applied bootstrap, when its settings are read back with the AWS CLI, then each matches the decisions above.
- Given the Owner's identity, when `aws sts get-caller-identity` runs, then the ARN is an IAM user, not root.

## Verification

**Commands:**
- `terraform -chdir=infra/bootstrap fmt -check` and `validate` -- expected: clean.
- `aws s3api get-bucket-versioning|get-public-access-block|get-bucket-encryption|get-bucket-ownership-controls|get-bucket-policy` -- expected: as decided.
- `aws iam get-role --role-name calls-log-deploy` -- expected: the trust policy exactly as decided.
- `aws lambda get-account-settings` -- expected: recorded before and after the request.
