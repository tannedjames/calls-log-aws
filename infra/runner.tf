# ---------------------------------------------------------------------------
# The runner (Story 3.7, AD-11, AD-12, AD-15): one Lambda Run per queued
# Folder, from the runner image, at most two at a time, no VPC.
#
#   calls-log-runs      SQS standard queue, visibility 1,200 s (> 900 s timeout)
#   calls-log-runs-dlq  crash backstop after 10 receives; it alarms
#   calls-log-runner    container Lambda, 900 s, memory from Story 3.1
#
# The role sits under /calls-log/ and carries the bootstrap's boundary, the
# only kind of role the deploy role may create.
# Cost: the queue, the trigger and an idle function are free; Runs are inside
# Lambda's 400,000 GB-s monthly free tier at the planned volume (Story 3.1
# measures it); logs kept 30 days are well under the free 5 GB.
# ---------------------------------------------------------------------------

locals {
  # Names and ARNs written out, not read from the resources, so the policy
  # documents can be built at plan time (tests/runner.tftest.hcl checks them).
  names = {
    runner     = "calls-log-runner"
    killswitch = "calls-log-killswitch"
    intake     = "calls-log-intake"
    runs       = "calls-log-runs"
    table      = "calls-log-folders"
  }
  arns = {
    runner_logs     = "arn:aws:logs:${var.region}:${var.account_id}:log-group:/aws/lambda/${local.names.runner}"
    killswitch_logs = "arn:aws:logs:${var.region}:${var.account_id}:log-group:/aws/lambda/${local.names.killswitch}"
    intake_logs     = "arn:aws:logs:${var.region}:${var.account_id}:log-group:/aws/lambda/${local.names.intake}"
    runner          = "arn:aws:lambda:${var.region}:${var.account_id}:function:${local.names.runner}"
    intake          = "arn:aws:lambda:${var.region}:${var.account_id}:function:${local.names.intake}"
    runs            = "arn:aws:sqs:${var.region}:${var.account_id}:${local.names.runs}"
    table           = "arn:aws:dynamodb:${var.region}:${var.account_id}:table/${local.names.table}"
    repository      = "arn:aws:ecr:${var.region}:${var.account_id}:repository/calls-log-runner"
  }
  image        = "${aws_ecr_repository.runner.repository_url}:${var.runner_image_tag}"
  boundary_arn = "arn:aws:iam::${var.account_id}:policy/calls-log-workload-boundary"
  # The functions' environment: exactly the names cloud/settings.py reads (AD-9).
  calls_log_env = {
    CALLS_LOG_BUCKET           = aws_s3_bucket.data.bucket
    CALLS_LOG_TABLE            = aws_dynamodb_table.folders.name
    CALLS_LOG_QUEUE_URL        = aws_sqs_queue.runs.url
    CALLS_LOG_ALERTS_TOPIC_ARN = aws_sns_topic.alerts.arn
  }
}

data "aws_iam_policy_document" "lambda_trust" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.account_id]
    }
  }
}

# ----- the queue -------------------------------------------------------------

resource "aws_sqs_queue" "runs_dlq" {
  name                      = "calls-log-runs-dlq"
  message_retention_seconds = 1209600 # 14 days, the most SQS keeps
  sqs_managed_sse_enabled   = true
}

resource "aws_sqs_queue" "runs" {
  name                       = local.names.runs
  visibility_timeout_seconds = 1200 # longer than a Run (900 s) and its lease (1,000 s)
  message_retention_seconds  = 1209600
  receive_wait_time_seconds  = 20
  sqs_managed_sse_enabled    = true

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.runs_dlq.arn
    maxReceiveCount     = 10 # a crash backstop only: attempts park a Folder after 3 Runs (AD-6)
  })
}

# ----- the role ----------------------------------------------------------------

resource "aws_iam_role" "runner" {
  name                 = local.names.runner
  path                 = "/calls-log/"
  permissions_boundary = local.boundary_arn
  assume_role_policy   = data.aws_iam_policy_document.lambda_trust.json
}

data "aws_iam_policy_document" "runner" {
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${local.arns.runner_logs}:*"]
  }

  # No s3:prefix condition, on purpose: a Run reads work/<uploadId>/<file>.json
  # for every image, and most don't exist yet. S3 answers a missing key with
  # 404 only to a caller allowed to list the bucket, otherwise 403, and a
  # GetObject request carries no s3:prefix to match. Listing shows key names
  # only; the objects stay limited below.
  statement {
    sid       = "ListUploadsAndCheckpoints"
    actions   = ["s3:ListBucket"]
    resources = [local.data_bucket_arn]
  }

  statement {
    sid       = "ReadUploadsCheckpointsAndDaySheets"
    actions   = ["s3:GetObject"]
    resources = ["${local.data_bucket_arn}/inbox/*", "${local.data_bucket_arn}/work/*", "${local.data_bucket_arn}/results/*"]
  }

  statement {
    sid       = "WriteCheckpointsAndDaySheets"
    actions   = ["s3:PutObject"]
    resources = ["${local.data_bucket_arn}/work/*", "${local.data_bucket_arn}/results/*"]
  }

  statement {
    sid       = "FolderStatus"
    actions   = ["dynamodb:GetItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem"]
    resources = [local.arns.table]
  }

  statement {
    sid       = "Settings"
    actions   = ["ssm:GetParameter", "ssm:GetParameters"]
    resources = ["arn:aws:ssm:${var.region}:${var.account_id}:parameter/calls-log/*"]
  }

  statement {
    sid       = "Queue"
    actions   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:ChangeMessageVisibility", "sqs:GetQueueAttributes"]
    resources = [local.arns.runs]
  }

  statement {
    sid       = "ParkedAlert"
    actions   = ["sns:Publish"]
    resources = [local.alerts_topic_arn]
  }

  statement {
    sid       = "Image"
    actions   = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"]
    resources = [local.arns.repository]
  }
}

resource "aws_iam_role_policy" "runner" {
  name   = "runner"
  role   = aws_iam_role.runner.id
  policy = data.aws_iam_policy_document.runner.json
}

# ----- the function --------------------------------------------------------------

resource "aws_cloudwatch_log_group" "runner" {
  name              = "/aws/lambda/${local.names.runner}"
  retention_in_days = 30 # AD-14
}

resource "aws_lambda_function" "runner" {
  function_name = local.names.runner
  role          = aws_iam_role.runner.arn
  package_type  = "Image"
  image_uri     = local.image
  architectures = ["x86_64"] # no aarch64 paddlepaddle wheel for Python 3.13
  memory_size   = var.runner_memory_mb
  timeout       = 900

  # The image's ENTRYPOINT (cloud/entrypoint.sh) links the baked models into
  # /tmp/paddlex, then runs this: Lambda's runtime client with the handler.
  image_config {
    command = ["/lambda-entrypoint.sh", "cloud.runner.handler"]
  }

  ephemeral_storage {
    size = 1024 # 40 images of up to 15 MB, plus the Day Sheet
  }

  # AD-11: the queue trigger's MaximumConcurrency (2) is the working cap; this
  # reservation is a second cap, possible because the account allows 400.
  reserved_concurrent_executions = 3

  environment {
    variables = local.calls_log_env
  }

  depends_on = [aws_cloudwatch_log_group.runner, aws_iam_role_policy.runner]
}

resource "aws_lambda_event_source_mapping" "runs" {
  event_source_arn        = aws_sqs_queue.runs.arn
  function_name           = aws_lambda_function.runner.arn
  batch_size              = 1
  function_response_types = ["ReportBatchItemFailures"] # Busy and failed Runs return their message

  scaling_config {
    maximum_concurrency = 2 # AD-11: at most two Runs at a time
  }

  # The kill switch turns this off outside Terraform. Turning it back on is a
  # runbook step, so a later deploy must not quietly re-enable it.
  lifecycle {
    ignore_changes = [enabled]
  }
}
