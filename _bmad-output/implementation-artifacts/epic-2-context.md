# Epic 2 Context: An AWS account you can change safely from GitHub, inside $2

<!-- Compiled from planning artifacts. Edit freely. Regenerate with compile-epic-context if planning docs change. -->

## Goal

Give the Owner one AWS account that is secured by default and changed only from code. After a one-time hand-applied bootstrap, every change reaches AWS through GitHub Actions authenticating with OIDC. No AWS access key exists anywhere, data is locked down by default, and cost alerts fire long before the $2 monthly cap. Epic 3's processing and Epic 4's upload page deploy onto this foundation.

## Stories

- Story 2.1: Account baseline, private repo and Terraform bootstrap
- Story 2.2: CI that tests, fails closed on private data, and checks OCR
- Story 2.3: Deploy pipeline: GitHub OIDC → ECR → Terraform apply
- Story 2.4: Data stores, secret parameters and the $2 budget

## Requirements & Constraints

- **Cost:** the total AWS bill is at most $2.00 a month (expected about $0.40). Only pay-per-use services; nothing with a fixed monthly fee (no NAT gateway, ALB, VPC endpoints, RDS, EKS, KMS customer keys, Secrets Manager or Route 53). A PR that adds a resource states its monthly cost.
- **Region:** `ap-southeast-2` (Sydney) for everything; data never leaves it. It's the AWS project's selected region, and the account's fixed policies refuse other regions apart from us-east-1/us-west-2 for global services (D-22).
- **Security baseline:**
  - Paid plan (the Free plan closes accounts after 6 months);
  - root MFA and no root access keys;
  - the Owner works through a non-root identity, with short-term CLI credentials via `aws login`;
  - least-privilege identities;
  - CI authenticates only via GitHub OIDC, limited to the repo's `main` branch with `aud = sts.amazonaws.com`.
- **Data protection (stores):** Block Public Access, SSE-S3, deny non-TLS, Object Ownership enforced, versioning where results live, region-locked.
- **Privacy gate:** must fail closed in CI when the private files are absent (they always are in CI).
- **Manual steps** for a new account are listed in one place: Paid plan, MFA, quota request, bootstrap apply, budget email confirmation.
- The Lambda concurrency quota increase is requested early; new accounts may start at 10.

## Technical Decisions

- **Terraform:** version ≥ 1.10; provider `hashicorp/aws ~> 6.56`; one root module `infra/` for `prod` with an S3 backend using `use_lockfile` (no DynamoDB lock table). `infra/bootstrap/` holds the state bucket, the GitHub OIDC provider, and the deploy and plan roles with their permissions. It is applied by hand, and re-applied after review when a story adds an AWS service (D-26). Everything else is applied only from GitHub Actions on `main`.
- **Naming:** resources `calls-log-<component>`; tags `project=calls-log`, `env=prod`, `managed-by=terraform`; SSM parameters `/calls-log/<snake_case>`.
- **CI auth:** `aws-actions/configure-aws-credentials@v6`. Only the role ARN is stored in GitHub, as a variable, never a key.
- **Secrets:** SSM SecureString parameters created with `ignore_changes` on value; real values are set out of band and never appear in Terraform state.
- **Repo:** private `tannedjames/calls-log-aws`, branch `main`; privacy hooks on (`core.hooksPath .githooks`).
- **Existing CI:** `.github/workflows/ci.yml` (since Story 2.2; it replaced `runner-image.yml`) builds and checks the runner image (Story 1.5); Story 2.2 extends CI, and Story 2.3 pushes the image to ECR.

## Cross-Story Dependencies

- 2.1 creates the state bucket and deploy role that 2.3's pipeline uses; 2.3's first resource is the ECR repository.
- 2.2's CI is a required check before 2.3 deploys.
- 2.4's stores, parameters and budget are deployed through 2.3's pipeline.
- Epic 3 needs 2.3 (image in ECR) and 2.4 (bucket, parameters).
