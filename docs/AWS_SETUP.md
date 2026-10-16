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
- account-wide S3 Block Public Access, so no bucket in the account can be
  made public. It lives here so the deploy role can never turn it off;
- GitHub's OIDC provider;
- the two roles GitHub Actions uses. No access key is ever created;
- `calls-log-workload-boundary`, the permissions boundary every function role
  carries (below).

| Role | Trusts only | Can |
|---|---|---|
| `calls-log-deploy` | runs on `main` of `tannedjames/calls-log-aws` | read and write the prod state; manage and push to the `calls-log-*` ECR repositories, and set the repository policy that lets Lambda pull from them; manage the data bucket's settings (not its objects, and it can't delete it), the `/calls-log/` parameters (not delete them), the `calls-log-*` topics (not subscribe anyone) and budgets; create and describe the `calls-log-*` tables (not delete them, nor read or write items); create roles only under the path `/calls-log/` and only with the workload boundary, and pass them only to Lambda; manage the `calls-log-*` functions, their queue triggers, queues, log groups and alarms |
| `calls-log-plan` | pull requests of that repo | read the prod state, describe those repositories and read their policies, and describe the `calls-log-*` tables (metadata, no items); nothing else |

**Function roles are fenced twice.** The roles the functions run as
(`calls-log-runner`, `calls-log-killswitch`, `calls-log-bench`) are created by
the deploy, from `infra/`. The deploy role may create a role only under
`/calls-log/` and only with `calls-log-workload-boundary` attached, so whatever
a merged commit puts in a function's policy, the role can never do more than
the boundary allows: this project's bucket, table, parameters, queues, alert
topic, image and logs, and turning the runner's queue trigger off. The deploy
role and the plan role sit at `/`, out of their own reach, and the boundary is
here, where only a person changes it.

A pull request can change the workflow it runs, so the plan role must stay
harmless in any hands. That is why pull-request plans run with
`-refresh=false`: refreshing reads every `/calls-log/` parameter with
decryption, so it would need the very secret the plan role must never see.

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

The bootstrap only works in calls-log's account: `account_id` defaults to
`333596351215`, and Terraform refuses credentials for any other account before
it reads or changes anything. A new account passes `-var account_id=<its id>`.

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
terraform -chdir=infra/bootstrap import aws_iam_role_policy.deploy_data_bucket calls-log-deploy:data-bucket
terraform -chdir=infra/bootstrap import aws_iam_role_policy.deploy_parameters calls-log-deploy:parameters
terraform -chdir=infra/bootstrap import aws_iam_role_policy.deploy_alerts calls-log-deploy:alerts
terraform -chdir=infra/bootstrap import aws_iam_role_policy.deploy_tables calls-log-deploy:tables
terraform -chdir=infra/bootstrap import aws_s3_account_public_access_block.account <account id>
terraform -chdir=infra/bootstrap import aws_iam_role.plan calls-log-plan
terraform -chdir=infra/bootstrap import aws_iam_role_policy.plan_read calls-log-plan:read-only-plan
terraform -chdir=infra/bootstrap import aws_iam_policy.workload_boundary arn:aws:iam::<account id>:policy/calls-log-workload-boundary
terraform -chdir=infra/bootstrap import aws_iam_role_policy.deploy_lambda_platform calls-log-deploy:lambda-platform
```

**From CloudShell, without the PC.** Any browser will do: CloudShell is a
terminal in the AWS console, already signed in, with nothing to install.
`infra/bootstrap/cloudshell.sh` checks the account, rebuilds the state by
importing what exists (read-only), and plans; a second run applies.

1. In the console, open calls-log's account (or project), region **Asia
   Pacific (Sydney)**, then CloudShell (top bar).
2. On GitHub, download the branch to apply as a ZIP (*Code → Download ZIP*).
   In CloudShell, remove any older ZIP (`rm -f ~/calls-log-aws-*.zip`), then
   *Actions → Upload file*.
3. Run it with `bash`, never pasted line by line, so an error stops the script
   and not the shell:
   ```bash
   rm -rf /tmp/cl && mkdir /tmp/cl && cd /tmp/cl && unzip -q ~/calls-log-aws-*.zip
   bash /tmp/cl/calls-log-aws-*/infra/bootstrap/cloudshell.sh
   ```
4. Read the plan. Only if it shows additions alone (0 to change, 0 to destroy):
   `bash /tmp/cl/calls-log-aws-*/infra/bootstrap/cloudshell.sh apply`.
5. *Actions → Download file* `bootstrap/terraform.tfstate`, and put it in the
   PC's `infra/bootstrap/` (keep the old one as a backup), or the PC's copy
   won't know what was just created.

It works in `/tmp`: the AWS provider is larger than CloudShell's 1 GB home.

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
- **Parameter values** (Story 2.4): Terraform creates the six `/calls-log/`
  parameters with placeholders only (`infra/parameters.tf`). Set the real
  values from this PC, where the private files are. The tool reads them
  itself and prints only parameter names, so no value lands in the shell
  history, the repo or the Terraform state:
  ```powershell
  .\.venv\Scripts\python.exe tools\put_parameters.py --from-private-files   # Web App URL, secret, NAME_TO_ID keys
  ```
  Run it again whenever an employee is added or the secret is rotated
  (Story 5.4). The upload page's passphrase hash is set in Story 4.1.
- **Alert email** (Story 2.4): the $2 budget, and later Parked Folders and the
  kill switch, email through the `calls-log-alerts` topic. Subscribe your
  address once; it is stored only in `/calls-log/alert_email` and the
  subscription, never in Terraform:
  ```powershell
  .\.venv\Scripts\python.exe tools\put_parameters.py --alert-email <your address>
  ```
  AWS then emails that address once: click *Confirm subscription*, or no
  alert ever arrives. To change the address, run it again with the new one,
  then remove the old one with the *unsubscribe* link in any alert email. Check with
  `aws sns list-subscriptions-by-topic --topic-arn (terraform -chdir=infra output -raw alerts_topic_arn)`.
- **Lambda cost per image** (Story 3.1, once, right after the first deploy
  with the functions): `enable_bench` starts on, so the deploy creates one
  benchmark function per memory size. Measure with made-up images only:
  ```powershell
  .\.venv\Scripts\python.exe tools\gen_synth.py
  .\.venv\Scripts\python.exe tools\gen_synth.py --out synthetic\big --images 40 --photos 0 --sideways 0
  .\.venv\Scripts\python.exe tools\bench_lambda.py "synthetic\Nguyen Van A & 2026-06-01" "synthetic\Nguyen Van A & 2026-06-01_photo" "synthetic\Nguyen Van A & 2026-06-01_sideways" --big "synthetic\big\Nguyen Van A & 2026-06-01" --out docs\aws\lambda-benchmark.md
  ```
  It prints and writes the report: GB-s per image at 1,536, 2,048 and
  3,008 MB, the month at 12,000 images against 350,000 GB-s, and whether a
  40-image Folder fits in one 900 s Run. Then a PR sets `runner_memory_mb` to
  the chosen size and `enable_bench = false` (in `infra/variables.tf`), with
  the report; merging it removes the benchmark functions. Exit code 3 means
  the plan doesn't hold, and the report says what follows.
- **AWS Sync Switch** (AD-10): off until the Owner turns it on.
  `tools\put_parameters.py --sync-enabled true` (or `false`). The PC's own
  switch stays in `config.py`.
