# AWS setup: the manual steps, in one place

Everything in AWS is created by Terraform, and after this page only GitHub
Actions changes it. These are the few steps a person has to do by hand, once
per AWS account, in this order. Region: **`ap-southeast-2` (Sydney)** for everything; it's the region this AWS
project was created in, and AWS refuses others (decision D-22).

## 1. The account (AWS console)

- [ ] **Paid plan.** *Billing and Cost Management* must show the Paid plan.
  The Free plan closes the account after 6 months; upgrading keeps the free
  credits. The design costs about $0.40 a month and is capped at $2.
- [ ] **Root MFA.** Account menu → *Security credentials* → *Assign MFA
  device*. Never create root access keys; the root user isn't used again
  after this section.
- [ ] **An admin user for daily work.** *IAM* → *Users* → *Create user*, with
  console access and a password, attached to `AdministratorAccess`. Sign in
  as that user once and add MFA to it too.

## 2. This PC

- [ ] AWS CLI 2.32 or later, then sign in as the admin user, not root. This
  gives short-term credentials that rotate every 15 minutes; no key is stored:
  ```powershell
  aws login
  aws sts get-caller-identity      # the Arn must end in :user/<name>, not :root
  ```
- [ ] Terraform 1.10 or later: `winget install -e --id Hashicorp.Terraform`.

## 3. Bootstrap (Terraform, applied by hand once)

`infra/bootstrap/` creates the Terraform state bucket
(`calls-log-tfstate-<account id>`: versioned, encrypted, no public access, TLS
only), GitHub's OIDC provider, and the deploy role `calls-log-deploy`. The role
trusts only GitHub runs for **pushes to `main` of `tannedjames/calls-log-aws`**.
No access key is ever created.

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
```

Note for the deploy workflow (Story 2.3): don't put the job in a GitHub
*environment*. That changes the token's subject to
`repo:…:environment:<name>`, which the role doesn't trust.

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

- **GitHub variable** (Story 2.3): store the deploy role's ARN as the
  repository *variable* `AWS_DEPLOY_ROLE_ARN`, never as a secret key:
  `gh variable set AWS_DEPLOY_ROLE_ARN --body "<deploy_role_arn output>"`.
- **Budget email** (Story 2.4): AWS emails the alert address once; click
  *Confirm subscription*, or the $2 budget alerts never arrive.
- **Secret values** (Story 2.4): set the Web App URL and secret in SSM
  Parameter Store by hand. They never go into Terraform or the repo.
