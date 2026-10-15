# Offline checks of the invariants that keep GitHub from changing AWS except
# from main: each CI role trusts one exact token subject, the plan role can
# only read, and the deploy role's ECR rights stay on calls-log-* repositories.
#
#   terraform -chdir=infra/bootstrap init -backend=false
#   terraform -chdir=infra/bootstrap test
#
# Plan only: nothing is created. The real provider builds the policy documents
# locally; fake credentials and the caller-identity override keep it from
# calling AWS.

provider "aws" {
  region                      = "ap-southeast-2"
  access_key                  = "offline-test"
  secret_key                  = "offline-test"
  skip_credentials_validation = true
  skip_requesting_account_id  = true
  skip_metadata_api_check     = true
}

override_data {
  target          = data.aws_caller_identity.current
  override_during = plan
  values          = { account_id = "123456789012" }
}

run "each_role_trusts_one_exact_subject" {
  command = plan

  assert {
    condition = jsondecode(data.aws_iam_policy_document.github_trust["deploy"].json).Statement == [{
      Effect    = "Allow"
      Action    = "sts:AssumeRoleWithWebIdentity"
      Principal = { Federated = "arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com" }
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          "token.actions.githubusercontent.com:sub" = "repo:tannedjames@185595532/calls-log-aws@1419265674:ref:refs/heads/main"
        }
      }
    }]
    error_message = "The deploy role must trust exactly repo:tannedjames@185595532/calls-log-aws@1419265674:ref:refs/heads/main, with StringEquals on sub and aud, and nothing else."
  }

  assert {
    condition = jsondecode(data.aws_iam_policy_document.github_trust["plan"].json).Statement == [{
      Effect    = "Allow"
      Action    = "sts:AssumeRoleWithWebIdentity"
      Principal = { Federated = "arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com" }
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          "token.actions.githubusercontent.com:sub" = "repo:tannedjames@185595532/calls-log-aws@1419265674:pull_request"
        }
      }
    }]
    error_message = "The plan role must trust exactly repo:tannedjames@185595532/calls-log-aws@1419265674:pull_request, with StringEquals on sub and aud, and nothing else."
  }
}

run "plan_role_can_only_read" {
  command = plan

  assert {
    condition = length(setsubtract(
      flatten([for s in jsondecode(data.aws_iam_policy_document.plan_read.json).Statement : s.Action]),
      ["s3:ListBucket", "s3:GetObject", "ecr:DescribeRepositories", "ecr:GetLifecyclePolicy", "ecr:ListTagsForResource"],
    )) == 0
    error_message = "The plan role runs code from any pull request: it may only list and read the state and describe repositories."
  }

  assert {
    condition = alltrue([
      for s in jsondecode(data.aws_iam_policy_document.plan_read.json).Statement :
      s.Resource == "arn:aws:s3:::calls-log-tfstate-123456789012/prod/terraform.tfstate"
      if contains(flatten([s.Action]), "s3:GetObject")
    ])
    error_message = "The plan role may read the prod state file only, not other objects."
  }
}

run "deploy_ecr_rights_stay_on_calls_log_repositories" {
  command = plan

  assert {
    condition = alltrue([
      for s in jsondecode(data.aws_iam_policy_document.deploy_ecr.json).Statement :
      s.Action == "ecr:GetAuthorizationToken" ? s.Resource == "*" :
      s.Resource == "arn:aws:ecr:ap-southeast-2:123456789012:repository/calls-log-*"
    ])
    error_message = "Only ecr:GetAuthorizationToken may use \"*\"; every other ECR right must be limited to calls-log-* repositories."
  }
}

run "a_wildcard_subject_is_refused" {
  command = plan

  variables {
    github_subject_prefix = "repo:tannedjames/*"
  }

  expect_failures = [var.github_subject_prefix]
}
