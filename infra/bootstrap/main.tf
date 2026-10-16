data "aws_caller_identity" "current" {}

locals {
  account_id   = data.aws_caller_identity.current.account_id
  state_bucket = "calls-log-tfstate-${local.account_id}"
  state_key    = "prod/terraform.tfstate"
  # Both ARNs have fixed formats. Writing them out, instead of reading them
  # from the resources, lets the IAM policy documents be built at plan time,
  # which the offline tests (tests/ci_roles.tftest.hcl) need.
  state_bucket_arn = "arn:aws:s3:::${local.state_bucket}"
  github_oidc_arn  = "arn:aws:iam::${local.account_id}:oidc-provider/token.actions.githubusercontent.com"
  # The one token subject each CI role trusts. Deploy: a run on main of this
  # repo (a push, or CI's workflow_run). Plan: a pull request. Any other
  # branch, event or repo (forks have other IDs) gets another subject, so STS
  # refuses it.
  github_subjects = {
    deploy = "${var.github_subject_prefix}:ref:refs/heads/main"
    plan   = "${var.github_subject_prefix}:pull_request"
  }
  # Every repository infra/ manages is named calls-log-<component>.
  ecr_repositories = "arn:aws:ecr:${var.region}:${local.account_id}:repository/calls-log-*"
  # What infra/ manages from Story 2.4 on (infra/data.tf, parameters.tf,
  # alerts.tf). The data bucket's name is fixed; everything else is
  # calls-log-* or under /calls-log/.
  data_bucket_arn = "arn:aws:s3:::calls-log-data-${local.account_id}"
  parameters      = "arn:aws:ssm:${var.region}:${local.account_id}:parameter/calls-log/*"
  topics          = "arn:aws:sns:${var.region}:${local.account_id}:calls-log-*"
  budgets         = "arn:aws:budgets::${local.account_id}:budget/calls-log-*"
}

# ---------------------------------------------------------------------------
# Account-wide Block Public Access (AD-18): no bucket in this account can be
# made public, whatever its own settings. It lives here, applied by hand,
# because the deploy role must never be able to turn it off.
# ---------------------------------------------------------------------------

resource "aws_s3_account_public_access_block" "account" {
  account_id              = local.account_id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ---------------------------------------------------------------------------
# Terraform state bucket: versioned, encrypted, never public, TLS only.
# A few KB of state: about $0.00 a month.
# ---------------------------------------------------------------------------

resource "aws_s3_bucket" "state" {
  bucket = local.state_bucket

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket = aws_s3_bucket.state.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_ownership_controls" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    id     = "expire-old-state-versions"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = 90
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  depends_on = [aws_s3_bucket_versioning.state]
}

data "aws_iam_policy_document" "state_tls_only" {
  statement {
    sid       = "DenyNonTLS"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.state.arn, "${aws_s3_bucket.state.arn}/*"]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "state" {
  bucket = aws_s3_bucket.state.id
  policy = data.aws_iam_policy_document.state_tls_only.json

  depends_on = [aws_s3_bucket_public_access_block.state]
}

# ---------------------------------------------------------------------------
# GitHub OIDC: GitHub Actions gets short-lived AWS credentials per run; no
# access key exists anywhere. IAM and OIDC are free.
# ---------------------------------------------------------------------------

resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
  # No thumbprint: AWS checks GitHub's certificate against its own trust store.
}

data "aws_iam_policy_document" "github_trust" {
  for_each = local.github_subjects

  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [local.github_oidc_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = [each.value]
    }
  }
}

resource "aws_iam_role" "deploy" {
  name                 = "calls-log-deploy"
  description          = "GitHub Actions on main of ${var.github_repo}, via OIDC. No keys."
  assume_role_policy   = data.aws_iam_policy_document.github_trust["deploy"].json
  max_session_duration = 3600

  depends_on = [aws_iam_openid_connect_provider.github]
}

# ---------------------------------------------------------------------------
# What the deploy role may do. These permissions live here, applied by hand
# after review, so the role can never widen itself: a story that adds an AWS
# service to infra/ adds its permissions here (docs/AWS_SETUP.md, section 3).
# ---------------------------------------------------------------------------

# The prod state and its lock file.
data "aws_iam_policy_document" "deploy_state" {
  statement {
    sid       = "ListStateBucket"
    actions   = ["s3:ListBucket"]
    resources = [local.state_bucket_arn]
  }

  statement {
    sid       = "ReadWriteProdState"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${local.state_bucket_arn}/prod/*"]
  }
}

resource "aws_iam_role_policy" "deploy_state" {
  name   = "terraform-state"
  role   = aws_iam_role.deploy.id
  policy = data.aws_iam_policy_document.deploy_state.json
}

# ECR (Story 2.3): manage the calls-log-* repositories and push images to them.
data "aws_iam_policy_document" "deploy_ecr" {
  statement {
    sid       = "EcrSignIn"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"] # this action has no resource-level form
  }

  statement {
    sid = "ManageRepositories"
    actions = [
      "ecr:CreateRepository",
      "ecr:DeleteRepository",
      "ecr:DescribeRepositories",
      "ecr:PutImageTagMutability",
      "ecr:PutImageScanningConfiguration",
      "ecr:PutLifecyclePolicy",
      "ecr:GetLifecyclePolicy",
      "ecr:DeleteLifecyclePolicy",
      "ecr:TagResource",
      "ecr:UntagResource",
      "ecr:ListTagsForResource",
    ]
    resources = [local.ecr_repositories]
  }

  statement {
    sid = "PushImages"
    # The push set from the ECR user guide, plus DescribeImages so a re-run
    # can see its tag already exists.
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:InitiateLayerUpload",
      "ecr:UploadLayerPart",
      "ecr:CompleteLayerUpload",
      "ecr:PutImage",
      "ecr:BatchGetImage",
      "ecr:DescribeImages",
    ]
    resources = [local.ecr_repositories]
  }
}

resource "aws_iam_role_policy" "deploy_ecr" {
  name   = "ecr"
  role   = aws_iam_role.deploy.id
  policy = data.aws_iam_policy_document.deploy_ecr.json
}

# The data bucket (Story 2.4): create it and manage its configuration, the
# calls the provider makes and nothing more. No object access, so the deploy
# role can't read or change uploads or Day Sheets, and no s3:DeleteBucket.
# s3:ListBucket is what HeadBucket needs; it lists keys, not contents.
data "aws_iam_policy_document" "deploy_data_bucket" {
  statement {
    sid = "ManageDataBucket"
    actions = [
      "s3:CreateBucket",
      "s3:ListBucket",
      "s3:GetBucket*",
      "s3:GetAccelerateConfiguration",
      "s3:GetEncryptionConfiguration",
      "s3:GetLifecycleConfiguration",
      "s3:GetReplicationConfiguration",
      "s3:PutBucketPublicAccessBlock",
      "s3:PutBucketOwnershipControls",
      "s3:PutEncryptionConfiguration",
      "s3:PutBucketVersioning",
      "s3:PutBucketPolicy",
      "s3:DeleteBucketPolicy",
      "s3:PutLifecycleConfiguration",
      "s3:PutBucketTagging",
      "s3:TagResource",
    ]
    resources = [local.data_bucket_arn]
  }
}

resource "aws_iam_role_policy" "deploy_data_bucket" {
  name   = "data-bucket"
  role   = aws_iam_role.deploy.id
  policy = data.aws_iam_policy_document.deploy_data_bucket.json
}

# The /calls-log/ parameters (Story 2.4). Terraform reads them back when it
# refreshes, so the deploy role can read them; its state never holds the
# values (write-only value_wo, infra/parameters.tf). No ssm:DeleteParameter:
# removing one is a deliberate change made here first.
data "aws_iam_policy_document" "deploy_parameters" {
  statement {
    sid = "ManageParameters"
    actions = [
      "ssm:PutParameter",
      "ssm:GetParameter",
      "ssm:GetParameters",
      "ssm:AddTagsToResource",
      "ssm:RemoveTagsFromResource",
      "ssm:ListTagsForResource",
    ]
    resources = [local.parameters]
  }

  statement {
    sid       = "DescribeParameters"
    actions   = ["ssm:DescribeParameters"]
    resources = ["*"] # this action has no resource-level form
  }
}

resource "aws_iam_role_policy" "deploy_parameters" {
  name   = "parameters"
  role   = aws_iam_role.deploy.id
  policy = data.aws_iam_policy_document.deploy_parameters.json
}

# The alert topic and the $2 budget (Story 2.4). Subscriptions are made out of
# band, so the deploy role can't add or change who gets alerts.
data "aws_iam_policy_document" "deploy_alerts" {
  statement {
    sid = "ManageTopics"
    actions = [
      "sns:CreateTopic",
      "sns:DeleteTopic",
      "sns:GetTopicAttributes",
      "sns:SetTopicAttributes",
      "sns:TagResource",
      "sns:UntagResource",
      "sns:ListTagsForResource",
    ]
    resources = [local.topics]
  }

  statement {
    sid = "ManageBudgets"
    actions = [
      "budgets:ViewBudget",
      "budgets:ModifyBudget",
      "budgets:TagResource",
      "budgets:UntagResource",
      "budgets:ListTagsForResource",
    ]
    resources = [local.budgets]
  }
}

resource "aws_iam_role_policy" "deploy_alerts" {
  name   = "alerts"
  role   = aws_iam_role.deploy.id
  policy = data.aws_iam_policy_document.deploy_alerts.json
}

# ---------------------------------------------------------------------------
# Plan role: what a pull request's `terraform plan` gets. A pull request can
# change the workflow it runs, so this role must be harmless in any hands:
# it reads the state and describes resources, and can't write anything, pull
# images, or read data or parameters. Plans run with -lock=false, so it never
# needs to write the lock file, and with -refresh=false, so it never reads the
# resources themselves: refreshing a parameter would decrypt the secret.
# ---------------------------------------------------------------------------

resource "aws_iam_role" "plan" {
  name                 = "calls-log-plan"
  description          = "GitHub Actions pull requests of ${var.github_repo}, via OIDC: read-only terraform plan."
  assume_role_policy   = data.aws_iam_policy_document.github_trust["plan"].json
  max_session_duration = 3600

  depends_on = [aws_iam_openid_connect_provider.github]
}

data "aws_iam_policy_document" "plan_read" {
  statement {
    sid       = "ListStateBucket"
    actions   = ["s3:ListBucket"]
    resources = [local.state_bucket_arn]
  }

  statement {
    sid       = "ReadProdState"
    actions   = ["s3:GetObject"]
    resources = ["${local.state_bucket_arn}/${local.state_key}"]
  }

  statement {
    sid = "DescribeRepositories"
    actions = [
      "ecr:DescribeRepositories",
      "ecr:GetLifecyclePolicy",
      "ecr:ListTagsForResource",
    ]
    resources = [local.ecr_repositories]
  }
}

resource "aws_iam_role_policy" "plan_read" {
  name   = "read-only-plan"
  role   = aws_iam_role.plan.id
  policy = data.aws_iam_policy_document.plan_read.json
}
