#!/usr/bin/env bash
# Apply the bootstrap from AWS CloudShell when the Owner's PC isn't at hand
# (docs/AWS_SETUP.md §3, "From CloudShell"). The bootstrap's own state lives on
# the PC, so this first rebuilds it here by importing what already exists in
# AWS (an import only reads), then plans. Nothing is applied until you have
# read the plan and run this again with "apply".
#
#   bash infra/bootstrap/cloudshell.sh          # rebuild the state, then plan
#   bash infra/bootstrap/cloudshell.sh apply    # apply that plan, keep the state
#
# Run it with bash, never pasted into the shell: an error then stops this
# script, not your CloudShell session.
set -euo pipefail
cd "$(dirname "$0")"
ACCOUNT=333596351215   # calls-log's account; versions.tf refuses any other too
TF_VERSION=1.16.5

A=$(aws sts get-caller-identity --query Account --output text)
if [ "$A" != "$ACCOUNT" ]; then
  echo "STOP: CloudShell is signed in to account $A, not calls-log's $ACCOUNT."
  echo "Switch to that account (or project) in the console, then open CloudShell again."
  exit 1
fi
echo "Account $A (calls-log)."

if [ ! -x /tmp/tfbin/terraform ]; then
  curl -sSLo /tmp/tf.zip "https://releases.hashicorp.com/terraform/${TF_VERSION}/terraform_${TF_VERSION}_linux_amd64.zip"
  unzip -oq /tmp/tf.zip -d /tmp/tfbin
fi
tf() { /tmp/tfbin/terraform "$@"; }

if [ "${1:-}" = apply ]; then
  tf apply -input=false -no-color bootstrap.tfplan
  mkdir -p ~/bootstrap
  cp terraform.tfstate ~/bootstrap/terraform.tfstate
  echo
  echo "Applied. The state is in ~/bootstrap/terraform.tfstate: download it and put it"
  echo "in the PC's infra/bootstrap/ (keep the old one as a backup)."
  exit 0
fi

tf init -input=false -no-color > /dev/null
rm -f terraform.tfstate terraform.tfstate.backup bootstrap.tfplan   # rebuilt from AWS below
bucket="calls-log-tfstate-$A"
must() { tf import -input=false -no-color "$1" "$2" > /dev/null && echo "  imported  $1"; }
maybe() {
  if tf import -input=false -no-color "$1" "$2" > /dev/null 2>&1; then
    echo "  imported  $1"
  else
    echo "  new       $1"
  fi
}

echo "Rebuilding the state from AWS (read-only):"
# In AWS since Story 2.3: each must import, or something is wrong; stop.
for r in aws_s3_bucket aws_s3_bucket_versioning aws_s3_bucket_public_access_block \
         aws_s3_bucket_server_side_encryption_configuration aws_s3_bucket_ownership_controls \
         aws_s3_bucket_lifecycle_configuration aws_s3_bucket_policy; do
  must "$r.state" "$bucket"
done
must aws_iam_openid_connect_provider.github "arn:aws:iam::$A:oidc-provider/token.actions.githubusercontent.com"
must aws_iam_role.deploy calls-log-deploy
must aws_iam_role_policy.deploy_state calls-log-deploy:terraform-state
must aws_iam_role_policy.deploy_ecr calls-log-deploy:ecr
must aws_iam_role.plan calls-log-plan
must aws_iam_role_policy.plan_read calls-log-plan:read-only-plan
# Added by Stories 2.4, 3.4 and 3.7: imported if an earlier apply made them, else planned.
maybe aws_s3_account_public_access_block.account "$A"
maybe aws_iam_role_policy.deploy_data_bucket calls-log-deploy:data-bucket
maybe aws_iam_role_policy.deploy_parameters calls-log-deploy:parameters
maybe aws_iam_role_policy.deploy_alerts calls-log-deploy:alerts
maybe aws_iam_role_policy.deploy_tables calls-log-deploy:tables
maybe aws_iam_policy.workload_boundary "arn:aws:iam::$A:policy/calls-log-workload-boundary"
maybe aws_iam_role_policy.deploy_lambda_platform calls-log-deploy:lambda-platform

echo
echo "Plan:"
if ! tf plan -input=false -no-color -out bootstrap.tfplan > plan.txt 2>&1; then
  cat plan.txt
  exit 1
fi
grep -E '^  # |^Plan:|^No changes' plan.txt || true
echo
echo "Full plan: $(pwd)/plan.txt"
echo "Apply only if the Plan line shows 0 to destroy and what the story's PR lists:"
echo "  bash $(pwd)/cloudshell.sh apply"
