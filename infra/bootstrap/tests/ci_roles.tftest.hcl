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

run "deploy_rights_on_the_data_stay_off_the_data" {
  command = plan

  assert {
    condition = alltrue([
      for s in jsondecode(data.aws_iam_policy_document.deploy_data_bucket.json).Statement :
      s.Resource == "arn:aws:s3:::calls-log-data-123456789012"
    ])
    error_message = "The deploy role's S3 rights must name the data bucket itself only: no objects, no other bucket."
  }

  assert {
    condition = length([
      for a in flatten([for s in jsondecode(data.aws_iam_policy_document.deploy_data_bucket.json).Statement : s.Action]) :
      a if a == "s3:*" || a == "s3:DeleteBucket" || strcontains(a, "Object")
    ]) == 0
    error_message = "The deploy role may not touch objects or delete the data bucket."
  }

  assert {
    condition = alltrue([
      for s in jsondecode(data.aws_iam_policy_document.deploy_parameters.json).Statement :
      s.Action == "ssm:DescribeParameters" ? s.Resource == "*" :
      s.Resource == "arn:aws:ssm:ap-southeast-2:123456789012:parameter/calls-log/*"
    ])
    error_message = "Only ssm:DescribeParameters may use \"*\"; every other SSM right must stay under /calls-log/."
  }

  assert {
    condition = !contains(
      flatten([for s in jsondecode(data.aws_iam_policy_document.deploy_parameters.json).Statement : s.Action]),
      "ssm:DeleteParameter",
    )
    error_message = "The deploy role may not delete a parameter: removing one is a reviewed bootstrap change."
  }

  assert {
    condition = toset([
      for s in jsondecode(data.aws_iam_policy_document.deploy_alerts.json).Statement : s.Resource
      ]) == toset([
      "arn:aws:sns:ap-southeast-2:123456789012:calls-log-*",
      "arn:aws:budgets::123456789012:budget/calls-log-*",
    ])
    error_message = "The deploy role's topic and budget rights must stay on calls-log-* resources."
  }

  assert {
    condition = length([
      for a in flatten([for s in jsondecode(data.aws_iam_policy_document.deploy_alerts.json).Statement : s.Action]) :
      a if startswith(a, "sns:Subscribe") || a == "sns:Publish"
    ]) == 0
    error_message = "The deploy role may not subscribe anyone to alerts, or publish them."
  }
}

run "no_bucket_in_the_account_can_be_public" {
  command = plan

  assert {
    condition = alltrue([
      aws_s3_account_public_access_block.account.block_public_acls,
      aws_s3_account_public_access_block.account.block_public_policy,
      aws_s3_account_public_access_block.account.ignore_public_acls,
      aws_s3_account_public_access_block.account.restrict_public_buckets,
    ])
    error_message = "All four account-wide Block Public Access settings must be on."
  }
}

run "deploy_table_rights_stay_on_calls_log_tables" {
  command = plan

  assert {
    condition = alltrue([
      for s in jsondecode(data.aws_iam_policy_document.deploy_tables.json).Statement :
      s.Resource == "arn:aws:dynamodb:ap-southeast-2:123456789012:table/calls-log-*"
    ])
    error_message = "The deploy role's DynamoDB rights must stay on calls-log-* tables."
  }

  assert {
    condition = length([
      for a in flatten([for s in jsondecode(data.aws_iam_policy_document.deploy_tables.json).Statement : s.Action]) :
      a if contains(["dynamodb:DeleteTable", "dynamodb:*"], a) || strcontains(a, "Item") || strcontains(a, "Query") || strcontains(a, "Scan")
    ]) == 0
    error_message = "The deploy role may not delete tables or read or write items."
  }
}
