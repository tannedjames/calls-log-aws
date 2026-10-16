# ---------------------------------------------------------------------------
# Cost guardrails (Story 3.7, AD-11, FR-18). The queue trigger already caps
# the runner at two Runs. If something still runs away, an alarm calls the
# kill switch, which turns the queue trigger off within minutes and emails the
# Owner; turning it back on is a runbook step (docs/TECHNICAL.md, Runbook).
#
#   calls-log-runner-dlq      a message reached the dead-letter queue (a crash loop)
#   calls-log-runner-errors   6+ function errors in 15 min (crashes, not slow Runs)
#   calls-log-runner-runaway  both Run slots busy for N 15-min periods in a row
#   calls-log-intake-runaway  600+ upload-page requests in 15 min (Epic 4)
#   calls-log-intake-busy     60+ s of upload-page compute in 15 min (Epic 4)
#                             either makes the kill switch throttle the page to 0
#
# N comes from the memory size so that, past the free tier, the worst case
# stays at or under $0.50 (the AC), counting one more period for the alarm to
# fire and the switch to act: at 2,048 MB that is 7 periods (1 h 45 min of
# both slots busy, about 3 times a normal day's peak) and $0.48.
# Cost: 5 alarms (10 are free) and a function that runs only when one fires.
# ---------------------------------------------------------------------------

locals {
  lambda_gb_second_usd   = 0.0000166667
  runaway_usd_per_period = 2 * (var.runner_memory_mb / 1024) * 900 * local.lambda_gb_second_usd
  runaway_periods        = max(2, floor(0.5 / local.runaway_usd_per_period) - 1)
  runaway_worst_case_usd = (local.runaway_periods + 1) * local.runaway_usd_per_period
  # Fixed-format ARNs, so the alarms are complete at plan time.
  guardrail_actions = [
    "arn:aws:lambda:${var.region}:${var.account_id}:function:${local.names.killswitch}",
    local.alerts_topic_arn,
  ]
}

# ----- the kill switch -----------------------------------------------------------

resource "aws_iam_role" "killswitch" {
  name                 = local.names.killswitch
  path                 = "/calls-log/"
  permissions_boundary = local.boundary_arn
  assume_role_policy   = data.aws_iam_policy_document.lambda_trust.json
}

data "aws_iam_policy_document" "killswitch" {
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${local.arns.killswitch_logs}:*"]
  }

  statement {
    sid       = "TurnTheRunnerTriggerOff"
    actions   = ["lambda:UpdateEventSourceMapping", "lambda:GetEventSourceMapping"]
    resources = ["*"] # mappings are named by UUID; the condition pins the runner

    condition {
      test     = "ArnLike"
      variable = "lambda:FunctionArn"
      values   = [local.arns.runner]
    }
  }

  statement {
    sid       = "FindTheTrigger"
    actions   = ["lambda:ListEventSourceMappings"]
    resources = ["*"] # no resource-level form
  }

  # Epic 4: the upload page's alarm turns the page off, nothing else.
  statement {
    sid       = "ThrottleTheUploadPage"
    actions   = ["lambda:PutFunctionConcurrency", "lambda:GetFunctionConcurrency"]
    resources = [local.arns.intake]
  }

  statement {
    sid       = "TellTheOwner"
    actions   = ["sns:Publish"]
    resources = [local.alerts_topic_arn]
  }

  statement {
    sid       = "Image"
    actions   = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"]
    resources = [local.arns.repository]
  }
}

resource "aws_iam_role_policy" "killswitch" {
  name   = "killswitch"
  role   = aws_iam_role.killswitch.id
  policy = data.aws_iam_policy_document.killswitch.json
}

resource "aws_cloudwatch_log_group" "killswitch" {
  name              = "/aws/lambda/${local.names.killswitch}"
  retention_in_days = 30
}

resource "aws_lambda_function" "killswitch" {
  function_name = local.names.killswitch
  role          = aws_iam_role.killswitch.arn
  package_type  = "Image"
  image_uri     = local.image
  architectures = ["x86_64"]
  memory_size   = 512
  timeout       = 60

  image_config {
    command = ["/lambda-entrypoint.sh", "cloud.killswitch.handler"]
  }

  reserved_concurrent_executions = 1

  environment {
    variables = merge(local.calls_log_env, {
      CALLS_LOG_RUNNER_FUNCTION = aws_lambda_function.runner.function_name
      CALLS_LOG_INTAKE_FUNCTION = aws_lambda_function.intake.function_name
    })
  }

  depends_on = [aws_cloudwatch_log_group.killswitch, aws_iam_role_policy.killswitch, aws_ecr_repository_policy.runner]
}

# ----- the alarms ----------------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "dlq" {
  alarm_name          = "calls-log-runner-dlq"
  alarm_description   = "A Folder message reached the dead-letter queue: the runner crashes on it. The kill switch turned the queue trigger off."
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  dimensions          = { QueueName = aws_sqs_queue.runs_dlq.name }
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.guardrail_actions
  depends_on          = [aws_lambda_function.killswitch, aws_sns_topic_policy.alerts]
}

resource "aws_cloudwatch_metric_alarm" "errors" {
  alarm_name          = "calls-log-runner-errors"
  alarm_description   = "6 or more runner errors in 15 minutes (crashes, not failed Runs). The kill switch turned the queue trigger off."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = aws_lambda_function.runner.function_name }
  statistic           = "Sum"
  period              = 900
  evaluation_periods  = 1
  threshold           = 6
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.guardrail_actions
  depends_on          = [aws_lambda_function.killswitch, aws_sns_topic_policy.alerts]
}

resource "aws_cloudwatch_metric_alarm" "runaway" {
  alarm_name          = "calls-log-runner-runaway"
  alarm_description   = "Both runner slots busy for ${local.runaway_periods} quarter-hours in a row. The kill switch turned the queue trigger off."
  namespace           = "AWS/Lambda"
  metric_name         = "ConcurrentExecutions"
  dimensions          = { FunctionName = aws_lambda_function.runner.function_name }
  statistic           = "Maximum"
  period              = 900
  evaluation_periods  = local.runaway_periods
  datapoints_to_alarm = local.runaway_periods
  threshold           = 2
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.guardrail_actions
  depends_on          = [aws_lambda_function.killswitch, aws_sns_topic_policy.alerts]
}

# Normal use is under 100 requests in 15 minutes (a status refresh a minute
# per open page, three calls per folder). 600 means a script, or a stranger
# with the link guessing passphrases; each guess costs a full PBKDF2.
resource "aws_cloudwatch_metric_alarm" "intake_runaway" {
  alarm_name          = "calls-log-intake-runaway"
  alarm_description   = "600 or more upload-page requests in 15 minutes. The kill switch set the page's reserved concurrency to 0."
  namespace           = "AWS/Lambda"
  metric_name         = "Invocations"
  dimensions          = { FunctionName = aws_lambda_function.intake.function_name }
  statistic           = "Sum"
  period              = 900
  evaluation_periods  = 1
  threshold           = 600
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.guardrail_actions
  depends_on          = [aws_lambda_function.killswitch, aws_sns_topic_policy.alerts]
}

# The slow version of the same: a guesser who stays under 600 requests still
# pays a full PBKDF2 (about 1.4 s at 512 MB) per wrong passphrase. Normal use
# is under 20 s of compute in 15 minutes, cold starts included. At 60 s a
# guesser gets about 40 tries per quarter-hour, and the most the page can cost
# while staying under it is about 86,000 GB-s a month.
resource "aws_cloudwatch_metric_alarm" "intake_busy" {
  alarm_name          = "calls-log-intake-busy"
  alarm_description   = "The upload page computed for 60 s or more in 15 minutes (normal use: under 20 s). The kill switch set its reserved concurrency to 0."
  namespace           = "AWS/Lambda"
  metric_name         = "Duration"
  dimensions          = { FunctionName = aws_lambda_function.intake.function_name }
  statistic           = "Sum"
  period              = 900
  evaluation_periods  = 1
  threshold           = 60000 # milliseconds
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.guardrail_actions
  depends_on          = [aws_lambda_function.killswitch, aws_sns_topic_policy.alerts]
}

# CloudWatch alarms may invoke the kill switch, each named alarm only.
resource "aws_lambda_permission" "alarms" {
  for_each = {
    dlq            = aws_cloudwatch_metric_alarm.dlq.arn
    errors         = aws_cloudwatch_metric_alarm.errors.arn
    runaway        = aws_cloudwatch_metric_alarm.runaway.arn
    intake_runaway = aws_cloudwatch_metric_alarm.intake_runaway.arn
    intake_busy    = aws_cloudwatch_metric_alarm.intake_busy.arn
  }

  statement_id  = "alarm-${each.key}"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.killswitch.function_name
  principal     = "lambda.alarms.cloudwatch.amazonaws.com"
  source_arn    = each.value
}
