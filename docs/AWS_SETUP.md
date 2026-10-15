# AWS setup: the manual steps, in one place

Everything in AWS is created by Terraform, and after this page only GitHub
Actions changes it. The one exception is the bootstrap (section 3), which a
person re-applies when a story changes what the CI roles may do. These are the
few steps a person has to do by hand, once per AWS account, in this order.
Region: **`ap-southeast-2` (Sydney)** for everything; it's the region this AWS
project was created in, and AWS refuses others (decision D-22).

## 1. The account (AWS console)

- [ ] **Paid plan.** *Billing and Cost Management* must show the Paid plan.
  The Free plan closes the account after 6 months; upgrading keeps the free
  credits. The design costs about $0.40 a month and is capped at $2.
- [ ] **Root MFA.** Account menu → *Security credentials* → *Assign MFA
  device*. Never create root access keys; the root user isn't used again
  after this section.
- [ ] **A non-root identity with MFA for daily work.** This account is a "new
  AWS experience" project. Its console sign-in (an AWS Builder ID with MFA)
  already gives `aws login` sessions as `AccountFullAccessRole`, so no IAM
  user is needed; its fixed policies refuse IAM console passwords anyway
  (decision D-21). In a classic account, create an IAM user with MFA instead.

## 2. This PC

- [ ] AWS CLI 2.32 or later, then sign in. This gives short-term credentials
  that rotate every 15 minutes; no key is stored:
  ```powershell
  aws login
  aws sts get-caller-identity      # an assumed role (or :user/<name>), never :root
  ```
- [ ] Terraform 1.10 or later: `winget install -e --id Hashicorp.Terraform`.

## 3. Bootstrap (Terraform, applied by hand)

`infra/bootstrap/` creates:
- the Terraform state bucket `calls-log-tfstate-<account id>`: versioned,
  encrypted, no public access, TLS only;
- GitHub's OIDC provider;
- the two roles GitHub Actions uses. No access key is ever created.

| Role | Trusts only | Can |
|---|---|---|
| `calls-log-deploy` | runs on `main` of `tannedjames/calls-log-aws` | read and write the prod state, and manage and push to the `calls-log-*` ECR repositories |
| `calls-log-plan` | pull requests of that repo | read the prod state and describe those repositories, nothing else |

A pull request can change the workflow it runs, so the plan role must stay
harmless in any hands.

**Token subjects.** Each role trusts one exact subject. This repo was created
after 2026-07-15, so GitHub writes the subject with immutable IDs:
`repo:tannedjames@185595532/calls-log-aws@1419265674:ref:refs/heads/main` for
`main`, and `…:pull_request` for pull requests. A repo deleted and re-created
under the same name gets new IDs, so it can't deploy. If the repo is ever
replaced, read its prefix and pass it as `github_subject_prefix`:

```powershell
gh api repos/tannedjames/calls-log-aws/actions/oidc/customization/sub --jq .sub_claim_prefix
```

**The deploy role's permissions live here**, not in `infra/`, so a merged
commit can never widen them. When a story adds an AWS service to `infra/`, it
adds the matching permissions here too. Apply that change by hand, after
reading the plan, **before** merging the story's PR. Otherwise the deploy
fails with `AccessDenied`.

```powershell
terraform -chdir=infra/bootstrap init
terraform -chdir=infra/bootstrap plan -out bootstrap.tfplan   # read it before applying
terraform -chdir=infra/bootstrap apply bootstrap.tfplan
terraform -chdir=infra/bootstrap output
```

The bootstrap's own state (`infra/bootstrap/terraform.tfstate`) stays on this
PC and is gitignored; back it up with the repo folder. If it is ever lost, the
resources are untouched; re-import them (replace `<account id>`):

```powershell
$b = "calls-log-tfstate-<account id>"
terraform -chdir=infra/bootstrap import aws_s3_bucket.state $b
terraform -chdir=infra/bootstrap import aws_s3_bucket_versioning.state $b
terraform -chdir=infra/bootstrap import aws_s3_bucket_public_access_block.state $b
terraform -chdir=infra/bootstrap import aws_s3_bucket_server_side_encryption_configuration.state $b
terraform -chdir=infra/bootstrap import aws_s3_bucket_ownership_controls.state $b
terraform -chdir=infra/bootstrap import aws_s3_bucket_lifecycle_configuration.state $b
terraform -chdir=infra/bootstrap import aws_s3_bucket_policy.state $b
terraform -chdir=infra/bootstrap import aws_iam_openid_connect_provider.github arn:aws:iam::<account id>:oidc-provider/token.actions.githubusercontent.com
terraform -chdir=infra/bootstrap import aws_iam_role.deploy calls-log-deploy
terraform -chdir=infra/bootstrap import aws_iam_role_policy.deploy_state calls-log-deploy:terraform-state
terraform -chdir=infra/bootstrap import aws_iam_role_policy.deploy_ecr calls-log-deploy:ecr
terraform -chdir=infra/bootstrap import aws_iam_role.plan calls-log-plan
terraform -chdir=infra/bootstrap import aws_iam_role_policy.plan_read calls-log-plan:read-only-plan
```

Never put a deploy job in a GitHub *environment*. That changes the token's
subject to `repo:…:environment:<name>`, which no role trusts.

## 4. Lambda concurrency quota (request once, early)

New accounts can start with as few as 10 concurrent Lambda executions. Setting
reserved concurrency for the runner and the upload page needs more. Approval
can take a few days:

```powershell
aws lambda get-account-settings --query AccountLimit.ConcurrentExecutions
aws service-quotas request-service-quota-increase --service-code lambda --quota-code L-B99A9384 --desired-value 1000
aws service-quotas list-requested-service-quota-change-history --service-code lambda
```

A higher limit costs nothing; Lambda bills per use.

## 5. Later steps (done by their stories)

- **GitHub variables** (Story 2.3): store both role ARNs as repository
  *variables*. They aren't secrets, and no AWS key is stored anywhere:
  ```powershell
  gh variable set AWS_DEPLOY_ROLE_ARN --repo tannedjames/calls-log-aws --body (terraform -chdir=infra/bootstrap output -raw deploy_role_arn)
  gh variable set AWS_PLAN_ROLE_ARN --repo tannedjames/calls-log-aws --body (terraform -chdir=infra/bootstrap output -raw plan_role_arn)
  ```
  After that, every pull request gets a plan comment, and each merge with
  green CI deploys (`.github/workflows/deploy.yml`, docs/TECHNICAL.md §10).
- **Showing that another branch is refused** (once, or any time): run the
  deploy job from a branch that isn't `main`. It must fail at *Sign in with
  the deploy role* with `Not authorized to perform
  sts:AssumeRoleWithWebIdentity`:
  `gh workflow run deploy.yml --repo tannedjames/calls-log-aws --ref <branch>`.
  Every pull request also shows the deploy role refusing its token, in the
  *Terraform plan* job.
- **Forks.** The repo setting "Run workflows from fork pull requests" stays
  off. A fork's own runs carry the fork's IDs in their subject, so STS refuses
  them too.
- **Budget email** (Story 2.4): AWS emails the alert address once; click
  *Confirm subscription*, or the $2 budget alerts never arrive.
- **Secret values** (Story 2.4): set the Web App URL and secret in SSM
  Parameter Store by hand. They never go into Terraform or the repo.
