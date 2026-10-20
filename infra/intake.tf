# ---------------------------------------------------------------------------
# The upload and status page (Epic 4, AD-17): calls-log-intake behind a
# public Function URL. The page needs no AWS credentials; every API call
# needs the passphrase (cloud/intake.py), and images go from the browser
# straight to S3 through presigned POSTs for exact keys under inbox/.
#
#   Function URL     auth NONE, with both statements AD-17 requires
#   bucket CORS      POST from the Function URL's origin only (data.tf)
#   guardrail        calls-log-intake-runaway (guardrails.tf) throttles it to 0
#
# Cost: a few thousand requests a month, each well under a second at 512 MB:
# inside Lambda's free tier. The Function URL itself is free.
# ---------------------------------------------------------------------------

resource "aws_iam_role" "intake" {
  name                 = local.names.intake
  path                 = "/calls-log/"
  permissions_boundary = local.boundary_arn
  assume_role_policy   = data.aws_iam_policy_document.lambda_trust.json
}

data "aws_iam_policy_document" "intake" {
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${local.arns.intake_logs}:*"]
  }

  # Counting an upload's images (inbox/<uploadId>/) and finding Day Sheets
  # (results/). Every listing carries its prefix; the page never reads a key
  # it hasn't listed, so the 404-needs-ListBucket rule doesn't arise here.
  statement {
    sid       = "ListUploadsAndDaySheets"
    actions   = ["s3:ListBucket"]
    resources = [local.data_bucket_arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["inbox/*", "results/*"]
    }
  }

  # A presigned POST acts with the signer's rights: these are the uploads.
  statement {
    sid       = "UploadScreenshots"
    actions   = ["s3:PutObject"]
    resources = ["${local.data_bucket_arn}/inbox/*"]
  }

  # A presigned GET likewise: the Day Sheet downloads.
  statement {
    sid       = "DownloadDaySheets"
    actions   = ["s3:GetObject"]
    resources = ["${local.data_bucket_arn}/results/*"]
  }

  statement {
    sid       = "FolderStatus"
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem"]
    resources = [local.arns.table]
  }

  # The index for the Status tab; the table itself for one Person-Day's
  # uploads (the Owner tab's Re-run, cloud/status.py uploads). No Scan.
  statement {
    sid       = "QueryUploads"
    actions   = ["dynamodb:Query"]
    resources = [local.arns.table, "${local.arns.table}/index/uploads-by-day"]
  }

  statement {
    sid       = "Settings"
    actions   = ["ssm:GetParameter", "ssm:GetParameters"]
    resources = ["arn:aws:ssm:${var.region}:${var.account_id}:parameter/calls-log/*"]
  }

  statement {
    sid       = "QueueReadyFolders"
    actions   = ["sqs:SendMessage"]
    resources = [local.arns.runs]
  }

  # The Owner tab (owner password): stop or start the runner's queue trigger,
  # as the kill switch and the runbook do. Only the runner's trigger.
  statement {
    sid       = "OwnerStopsOrStartsTheRunner"
    actions   = ["lambda:UpdateEventSourceMapping", "lambda:GetEventSourceMapping"]
    resources = ["*"] # mappings are named by UUID; the condition pins the runner

    condition {
      test     = "ArnLike"
      variable = "lambda:FunctionArn"
      values   = [local.arns.runner]
    }
  }

  statement {
    sid       = "FindTheRunnerTrigger"
    actions   = ["lambda:ListEventSourceMappings"]
    resources = ["*"] # no resource-level form
  }

  # The Owner tab's settings (Story 4.5 part B), named one by one as in the
  # boundary: never webapp_url or sync_secret.
  statement {
    sid     = "OwnerChangesSettings"
    actions = ["ssm:PutParameter"]
    resources = [for name in ["sync_enabled", "intake_passphrase_hash", "owner_passphrase_hash", "name_prefixes"] :
    "arn:aws:ssm:${var.region}:${var.account_id}:parameter/calls-log/${name}"]
  }

  statement {
    sid       = "OwnerManagesAlertEmails"
    actions   = ["sns:Subscribe", "sns:Unsubscribe", "sns:ListSubscriptionsByTopic"]
    resources = [local.alerts_topic_arn]
  }

  statement {
    sid       = "Image"
    actions   = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"]
    resources = [local.arns.repository]
  }
}

resource "aws_iam_role_policy" "intake" {
  name   = "intake"
  role   = aws_iam_role.intake.id
  policy = data.aws_iam_policy_document.intake.json
}

resource "aws_cloudwatch_log_group" "intake" {
  name              = "/aws/lambda/${local.names.intake}"
  retention_in_days = 30 # AD-14
}

resource "aws_lambda_function" "intake" {
  function_name = local.names.intake
  role          = aws_iam_role.intake.arn
  package_type  = "Image"
  image_uri     = local.image
  architectures = ["x86_64"]
  memory_size   = 512
  timeout       = 30

  image_config {
    command = ["/lambda-entrypoint.sh", "cloud.intake.handler"]
  }

  # AD-11: two at a time is plenty for one Operator, and caps what a stranger
  # with the link can spend. The kill switch sets it to 0 (guardrails.tf);
  # a deploy must not quietly undo that, so changes after creation are
  # ignored and turning the page back on is a runbook step.
  reserved_concurrent_executions = 2

  environment {
    variables = merge(local.calls_log_env, {
      CALLS_LOG_RUNNER_FUNCTION = aws_lambda_function.runner.function_name # the Owner tab's stop/start
    })
  }

  lifecycle {
    ignore_changes = [reserved_concurrent_executions]
  }

  depends_on = [aws_cloudwatch_log_group.intake, aws_iam_role_policy.intake, aws_ecr_repository_policy.runner]
}

resource "aws_lambda_function_url" "intake" {
  function_name      = aws_lambda_function.intake.function_name
  authorization_type = "NONE" # the passphrase guards the API (AD-17)
}

# AD-17: a public Function URL needs both statements, or every call is 403.
resource "aws_lambda_permission" "intake_url" {
  statement_id           = "public-url"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = aws_lambda_function.intake.function_name
  principal              = "*"
  function_url_auth_type = "NONE"
}

resource "aws_lambda_permission" "intake_invoke" {
  statement_id             = "public-url-invoke"
  action                   = "lambda:InvokeFunction"
  function_name            = aws_lambda_function.intake.function_name
  principal                = "*"
  invoked_via_function_url = true # only through the URL, never a direct Invoke
}
