---
title: 'Story 2.3: Deploy pipeline: GitHub OIDC → ECR → Terraform apply'
type: 'feature'
created: '2026-10-15'
status: 'done'
route: 'dispatch'
review_loop_iteration: 0
baseline_commit: 'cb7ef79eea0cf0fe7b423c611c6477352fded53d'
context: ['{project-root}/_bmad-output/implementation-artifacts/epic-2-context.md']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Nothing deploys yet. Also, the deploy role can't be used as it stands. This repo was created after 2026-07-15, so GitHub signs its tokens with the **immutable subject** `repo:tannedjames@185595532/calls-log-aws@1419265674:…`. The API reports `use_immutable_subject: true` and that prefix. The role trusts `repo:tannedjames/calls-log-aws:ref:refs/heads/main`, so STS would refuse even `main`. Pull requests can't plan either, because the only role trusts `main` alone.

**Approach:**
- **Bootstrap:** the Owner re-applies it by hand after seeing the plan. It moves the deploy trust to the immutable subject and adds a read-only plan role for PRs.
- **`infra/`:** a new root module whose first resource is the ECR repository.
- **`deploy.yml`:** plans on every PR and posts the plan. Once CI is green on `main`, it applies and pushes the runner image tagged with the git SHA.

**Decisions:**
- **Subjects:** bootstrap variable `github_subject_prefix` (default above; read it with `gh api repos/OWNER/REPO/actions/oidc/customization/sub --jq .sub_claim_prefix`).
  - Deploy role: `StringEquals sub = <prefix>:ref:refs/heads/main`.
  - Plan role `calls-log-plan`: `StringEquals sub = <prefix>:pull_request`.
  - Both also require `aud = sts.amazonaws.com`, and sessions last at most 1 h.
- **Plan role, read-only:**
  - state `s3:ListBucket` and `s3:GetObject` on `prod/terraform.tfstate` only, so PR plans run with `-lock=false`;
  - `ecr:DescribeRepositories`, `GetLifecyclePolicy`, `ListTagsForResource` on `repository/calls-log-*`.
  - No access to images, data or parameters: a PR can change the workflow, so this role must stay harmless.
- **`infra/`:**
  - S3 backend: `calls-log-tfstate-333596351215`, key `prod/terraform.tfstate`, `use_lockfile`, `encrypt`. Same versions and default tags as the bootstrap.
  - ECR repository `calls-log-runner`: tags `IMMUTABLE`, basic scan on push (free), AES256, `force_delete = false`.
  - Lifecycle rule: keep the newest 2 images, any tag status.
  - The lock file covers `linux_amd64` and `windows_amd64`.
  - `infra/tests/*.tftest.hcl` uses a mock provider (no AWS) to assert IMMUTABLE and a count of 2.
- **`deploy.yml`** (concurrency group `deploy`, never cancelled):
  - **`plan` job, on `pull_request`:**
    - `fmt -check -recursive`, then `validate` for `infra/` and `infra/bootstrap/`, then `terraform test`;
    - `plan`, posted to the job summary and as one PR comment, edited on each push;
    - a step that requests the deploy role and **must be refused**. It prints only the token's `sub`, never the token.
    - Dependabot PRs skip the AWS steps, because they get read-only tokens.
  - **`deploy` job, on `workflow_run` of CI:**
    - runs only if the run succeeded, was a `push` event, on `main`, from this repo;
    - checks out `workflow_run.head_sha`, then `plan -out` and `apply`;
    - builds the image, runs `run_tests.py` inside it, then pushes `calls-log-runner:<full sha>`;
    - skips the build and push when that tag already exists (a re-run).
  - `workflow_dispatch` runs `deploy` too: it works on `main`, and STS refuses it anywhere else, which is the branch proof.
- **Pins:**
  - `aws-actions/configure-aws-credentials` v6.3.0 and `hashicorp/setup-terraform` v4.0.1, by commit SHA;
  - Terraform 1.16.5, the same as this PC;
  - ECR login through the runner's AWS CLI.
- **Deploy permissions live in the bootstrap** (Owner, 2026-10-15). The Owner reviews and hand-applies a small bootstrap plan whenever a story adds an AWS service, so the deploy role can never widen itself. AD-13 becomes "humans apply only the bootstrap: state bucket, OIDC provider, the two CI roles and their permissions".
  - Now: `ecr:GetAuthorizationToken` (resource `*`). Then manage and push on `repository/calls-log-*`: Create, Delete and Describe repositories; PutImageTagMutability; PutImageScanningConfiguration; Put, Get and Delete LifecyclePolicy; Tag, Untag and ListTagsForResource; the 5 layer and image push actions; DescribeImages.
- **GitHub:** the variables `AWS_DEPLOY_ROLE_ARN` and `AWS_PLAN_ROLE_ARN`. No new secrets.
- **Forks:** "Run workflows from fork pull requests" is off (verified). A fork's own token carries other IDs, so STS refuses it.
- **Cost:**
  - ECR about **$0.16 a month**: 2 images × about 0.8 GB at $0.10 per GB-month. The design estimated $0.24.
  - IAM, OIDC and scanning are free.
  - Actions: about 5 minutes per merge and about 1 minute per PR, against 2,000 free minutes a month.

## Boundaries & Constraints

**Always:**
- `ap-southeast-2`;
- every CI identity trusts exactly one subject, with `StringEquals`;
- the bootstrap is applied only after the Owner approves its plan;
- changes go through a branch and a PR;
- run `check_private_data.py` before each commit and push.

**Never:**
- an AWS access key, or a secret holding an ARN;
- `StringLike` or wildcard subjects;
- a GitHub *environment* on these jobs (it changes `sub`);
- the plan role getting any write, `ecr:BatchGetImage`, S3 data or SSM access;
- `terraform apply` from a PR or from this PC for `infra/`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Merge, CI green | `workflow_run` success, push, `main` | apply, then push `:<sha>` | Apply failure → job red, no push |
| CI red or cancelled on `main` | conclusion ≠ success | `deploy` skipped | N/A |
| PR | `pull_request` | plan posted, nothing applied; deploy role refused | Plan error → posted as well |
| Dispatch from a branch | `sub = …:ref:refs/heads/<branch>` | STS `AccessDenied` | Job red (the proof) |
| Re-run for the same SHA | tag exists | build and push skipped | IMMUTABLE would reject it anyway |
| Two merges in a row | CI cancels the older run on `main` | only the newer SHA deploys | `deploy` group queues, never cancels |

</frozen-after-approval>

## Code Map

- `infra/bootstrap/main.tf:8` `github_subject` -- replace with the prefix variable; `:118` `deploy_trust`, `:150` `deploy_state` -- reuse the pattern for the plan role; keep the state bucket untouched (`prevent_destroy`).
- `infra/bootstrap/{variables,outputs}.tf` -- add `github_subject_prefix` and `plan_role_arn`.
- `.github/workflows/ci.yml` -- unchanged. Its name `CI` is the `workflow_run` trigger. It cancels older runs per ref, which also orders deploys. Reuse its SHA-pinned checkout.
- `Dockerfile`, `.dockerignore` -- unchanged. Build with `--platform linux/amd64`. CI already checks for private files in the image.
- `docs/AWS_SETUP.md` §3, §5 -- the immutable subject, the plan role, re-applying when CI permissions change, the two variables.
- `docs/TECHNICAL.md` §10 -- add `deploy.yml`.
- `.gitignore:125-132` -- already ignores state, plans and `.terraform/`.

## Tasks & Acceptance

**Execution:**
- [x] `infra/bootstrap/{main,variables,outputs}.tf` -- immutable subjects, plan role, the deploy role's ECR permissions -- STS must accept `main` and PRs.
- [x] `infra/{versions,main,ecr,outputs}.tf`, `.terraform.lock.hcl`, `tests/ecr.tftest.hcl` -- the root module with ECR.
- [x] `.github/workflows/deploy.yml` -- plan and deploy jobs as decided.
- [x] `docs/AWS_SETUP.md`, `docs/TECHNICAL.md`, `_bmad-output/project-context.md` (D-26), AD-13 in the spine -- record the changes.
- [x] Bootstrap plan → the Owner approves → apply (`aws login` asked first); set both variables with `gh variable set`.
- [x] PR → plan comment and deploy-role refusal → merge → first deploy; dispatch from a branch → refused.

**Acceptance Criteria:**
- Given `infra/`, when `terraform fmt -check`, `validate` and `test` run, then all pass with no AWS access.
- Given the first deploy, when ECR is inspected, then `calls-log-runner` is IMMUTABLE, its lifecycle keeps 2, and the image tag is the merge's full SHA.
- Given the GitHub settings, when listed, then the only secret is `PRIVATE_DENYLIST` and the role ARNs are variables.
- Given the deploy, when `terraform plan` runs again on `main`, then there is no drift.

## Implementation Notes

- 2026-10-15, built inline (no subagents, the Owner's standing choice).
- **Bootstrap:**
  - one `github_trust` policy document per role (`for_each` over `local.github_subjects`). Data sources only, so no role is replaced; `aws_iam_role.deploy` keeps its address.
  - New: `aws_iam_role.plan` with `aws_iam_role_policy.plan_read` ("read-only-plan"), and `aws_iam_role_policy.deploy_ecr` ("ecr").
  - `github_subject_prefix` rejects wildcards and colons.
- **Small departures from the spec text:**
  - The deploy role also gets `ecr:BatchGetImage`. The ECR user guide's push policy (image-push-iam) lists it with the 5 push actions. The plan role still has no image access.
  - `infra/` keeps the provider in `versions.tf` and has a `variables.tf` instead of a `main.tf`.
  - The provider sets `allowed_account_ids = ["333596351215"]`, so a plan with any other account's credentials stops at once.
- **`deploy.yml`:**
  - Every `${{ }}` value reaches scripts through `env`, never pasted into the script text.
  - `oidc-check.sh` masks the token and prints only its `sub`. On a refusal it prints STS's error; on an unexpected success it prints nothing, because the output would hold credentials.
  - The image is built with `--provenance=false --sbom=false`, so ECR gets one image manifest, the kind Lambda runs.
- **Verified locally** (Terraform 1.16.5, provider 6.69.0):
  - `fmt -check` clean; `validate` passes for `infra/` and `infra/bootstrap/`; `terraform test` gives 2 passed.
  - With `countNumber = 3` and `MUTABLE`, both runs fail with their messages; restored afterwards.
  - Both lock files have `h1:` hashes for `linux_amd64` and `windows_amd64`.
  - Both workflows parse as YAML. The JWT subject decoder works on a made-up token.
  - `run_tests.py`: 128 passed. `check_private_data.py`: OK, 318 files.
- **Matrix rows:** only live runs can show these (PR, merge, branch dispatch, re-run). They are done after the bootstrap apply, and the results are recorded here.
- **Live, 2026-10-15:**
  - **Bootstrap:** the Owner approved the plan (3 add, 1 change: only the deploy trust's `sub`, 0 destroy) and it was applied from this PC; the session was already active, so no `aws login` was needed.
    - `iam get-role` shows both trusts exactly as designed, and `calls-log-plan`'s only policy is `read-only-plan` with 3 statements.
    - A fresh plan says "No changes".
    - The variables `AWS_DEPLOY_ROLE_ARN` and `AWS_PLAN_ROLE_ARN` are set; `PRIVATE_DENYLIST` is still the only secret.
  - **PR [#5](https://github.com/tannedjames/calls-log-aws/pull/5), run 38701791721 (Terraform plan, green):**
    - token subject `repo:tannedjames@185595532/calls-log-aws@1419265674:pull_request`, so the inferred PR form is confirmed;
    - the deploy role was refused: `AccessDenied … Not authorized to perform sts:AssumeRoleWithWebIdentity`;
    - the plan role signed in (`plan-pr-5`);
    - offline tests 2 + 4 passed;
    - the plan comment shows 2 to add (repository and lifecycle policy).
    - CI's two required checks were green; merged as `c008824`.
  - **First deploy, run 38702684063 (`workflow_run` after CI green on `main`, 3 min 17 s):**
    - subject `…:ref:refs/heads/main`;
    - "Apply complete! Resources: 2 added"; "infra/ matches AWS." (drift check);
    - 128 unit tests passed inside the image;
    - pushed `calls-log-runner:c008824ec4d82b442360ee0abd8494cdd2a7f917`, digest `sha256:327acc2d…f9df69`.
  - **ECR, checked with the CLI:**
    - IMMUTABLE, scan on push, AES256, tags project/env/managed-by;
    - the lifecycle rule is exactly "keep newest 2, any status";
    - one image, manifest `application/vnd.docker.distribution.manifest.v2+json` (a single image manifest that Lambda accepts), **767 MB** compressed, so 2 images cost about $0.14 a month.
  - **Branch proof, run 38702322804** (`gh workflow run deploy.yml --ref proof-sts-refuses-branch`):
    - subject `…:ref:refs/heads/proof-sts-refuses-branch`;
    - *Sign in with the deploy role* failed with `Could not assume role with OIDC: Not authorized to perform sts:AssumeRoleWithWebIdentity`, after the action's retries;
    - nothing after it ran. The branch was deleted afterwards.
  - **Basic scan of the first image:** 0 critical, 15 high, 20 medium, 5 low. All the high ones are Amazon Linux OS packages from the pinned base image: curl/libcurl-minimal, libX11 (pulled in by mesa-libGL), the openssl FIPS and snapsafe libs, pcre2, and rpm. Recorded in deferred work.
  - **Matrix coverage:**
    - merge with CI green: run 38702684063;
    - PR: run 38701791721;
    - dispatch from a branch: run 38702322804;
    - CI red or cancelled, and two merges in a row: the job guard and the concurrency settings, not exercised live;
    - re-run for the same SHA: the `describe-images` branch, not exercised live.
  - **Cost note:** from now on every merge to `main`, even docs only, pushes a new SHA-tagged image, and ECR keeps the newest 2.

## Spec Change Log

## Review Triage Log

Pass 1, 2026-10-15. Three lenses run inline (the Owner's choice): blind hunter (diff 43.5 kB, floor 7), edge-case hunter with the claims check, and verification gap.

- **medium (patched):** a manual run on `main` deployed `github.sha` without looking at CI. Admin bypass inside PRs can put a red commit on `main`, which breaks FR-24 ("a failed gate blocks the deploy"). Fixed: a step after sign-in requires CI green on a push for that SHA (`gh run list`, `actions: read`). It sits after sign-in so the branch proof still shows STS refusing. Checked against the real repo: `cb7ef79` gives `success`; a SHA that doesn't exist gives `no CI run`.
- **medium (patched, verification gap):** no test pinned the CI roles' trust or the plan role's read-only rights. A wider subject, `StringLike`, or a write action would have passed every check until a live run.
  - Added `infra/bootstrap/tests/ci_roles.tftest.hcl` (4 runs) with the real provider, fake keys and an overridden caller identity, and CI runs it.
  - Each of 3 mutations (plan role `s3:PutObject`, `StringLike` trust, ECR rights on `*`) fails the right run.
  - To make this possible, the policy documents now build the bucket and OIDC provider ARNs from the account ID, and both roles `depends_on` the provider. The JSON is unchanged.
  - Terraform 1.16.5 crashed (marks error) while printing a failed `for` over the data-source map, so each role got its own assertion.
- **low (patched):** an `init` error in the PR plan failed the step before `comment.md` existed, so it never reached the PR, which the matrix row says it should. `init` and `plan` now write to `plan.txt` together.
- **low (patched):** a branch dispatch shared the `deploy` concurrency group and could replace a waiting deploy of `main`. The group is now `deploy-${{ github.ref }}`.
- **low (patched, verification gap):** the "no drift" AC had no automated check. The deploy now plans again with `-detailed-exitcode` after the apply and fails on drift.
- **low (patched):** `oidc-check.sh` with a mistyped flag exited 0 and skipped the refusal check quietly. It now accepts only no arguments or exactly `--expect-refused ROLE`; tested with 5 argument lists.
- **low (patched):** renaming CI to anything other than `CI` silently stops deploys (`workflow_run` matches by name). Added a comment at `name: CI`.
- **low (patched):** TECHNICAL.md §10 said the tests cover `infra/` only.
- **false:** "the refusal check has no region before `configure-aws-credentials` runs." The workflow sets `AWS_REGION` and AWS CLI v2 reads it (AWS CLI env-var reference: it overrides `AWS_DEFAULT_REGION`).
- **low (rejected):** `gh pr comment --edit-last` would edit another bot comment if some other workflow ever posted one. No other workflow comments, and the fix adds code.
- **low (rejected):** the plan role's `s3:ListBucket` covers the whole state bucket. The bucket holds only `prod/` state; the bootstrap state is local.
- **low (rejected):** any workflow on `main` triggered by other events (schedule, issue_comment) could ask for the deploy role, because the subject names only the ref. Only `deploy.yml` requests `id-token`; adding one needs a PR with green CI; the repo is private with one collaborator. Narrowing it would need a claim beyond `sub`.
- **low (rejected, fix would edit the spec):** the frozen decision says "the 5 layer and image push actions", but the code also grants `ecr:BatchGetImage`. AWS's documented push policy (ECR user guide, image-push-iam) lists it with the 5. Without it, a push from a client that checks the manifest first would fail on the first deploy. The deploy role reading its own images isn't a meaningful extra right. Raised with the Owner at presentation.

## Design Notes

- `workflow_run` runs the workflow file from `main` with `sub = …:ref:refs/heads/main`. The guard on event, branch and repository keeps a fork PR whose branch is named `main` from deploying.
- Epic 3 will reorder the steps: push first, then apply the Lambda with the new image.
- A note for 2.4: refreshing `aws_ssm_parameter` writes the real value into state even with `ignore_changes`. Check for a write-only `value_wo` or keep the values out of Terraform, and the plan role must not get `ssm:GetParameter`.

## Verification

**Commands:**
- `terraform -chdir=infra fmt -check -recursive`, `init -backend=false`, `validate` and `test` -- all pass.
- `terraform -chdir=infra/bootstrap plan` -- only the trust policy changes, plus the new plan role and policies.
- `aws ecr describe-repositories`, `get-lifecycle-policy`, `describe-images` -- as in the ACs; the image's manifest type is one Lambda accepts.
- `gh run view` -- PR plan green with "deploy role refused"; the deploy green; the branch dispatch red with `AccessDenied`.
- `.venv\Scripts\python.exe -X utf8 run_tests.py` and `tools\check_private_data.py` -- OK.
