# Offline checks of the runner, its queue, its role and the cost guardrails
# (Stories 3.1 and 3.7):
#   terraform -chdir=infra init -backend=false
#   terraform -chdir=infra test
#
# Plan only, with fake credentials: nothing is created and AWS isn't called.

provider "aws" {
  region                      = "ap-southeast-2"
  access_key                  = "offline-test"
  secret_key                  = "offline-test"
  skip_credentials_validation = true
  skip_requesting_account_id  = true
  skip_metadata_api_check     = true
}

variables {
  runner_image_tag = "0123456789abcdef0123456789abcdef01234567"
}

run "the_runner_is_capped_and_runs_the_handler" {
  command = plan

  # Known only after apply; fixed so the redrive policy can be read at plan time.
  override_resource {
    target          = aws_sqs_queue.runs_dlq
    override_during = plan
    values          = { arn = "arn:aws:sqs:ap-southeast-2:333596351215:calls-log-runs-dlq" }
  }

  assert {
    condition = (
      aws_lambda_function.runner.timeout == 900 &&
      aws_lambda_function.runner.package_type == "Image" &&
      aws_lambda_function.runner.architectures == tolist(["x86_64"]) &&
      aws_lambda_function.runner.image_config[0].command == tolist(["/lambda-entrypoint.sh", "cloud.runner.handler"]) &&
      aws_lambda_function.runner.memory_size == 2048
    )
    error_message = "The runner must be the image's handler, x86_64, 900 s, at the chosen memory."
  }

  assert {
    condition     = length(aws_lambda_function.runner.vpc_config) == 0
    error_message = "No VPC (AD-12): it would need a NAT gateway to reach Google."
  }

  assert {
    condition = (
      aws_lambda_event_source_mapping.runs.batch_size == 1 &&
      aws_lambda_event_source_mapping.runs.scaling_config[0].maximum_concurrency == 2 &&
      contains(aws_lambda_event_source_mapping.runs.function_response_types, "ReportBatchItemFailures")
    )
    error_message = "One Folder per Run, at most two Runs at a time, and Busy or failed Runs return their message."
  }

  assert {
    condition = (
      aws_sqs_queue.runs.visibility_timeout_seconds == 1200 &&
      jsondecode(aws_sqs_queue.runs.redrive_policy).maxReceiveCount == 10
    )
    error_message = "Visibility 1,200 s (longer than a Run and its lease) and the dead-letter queue after 10 receives."
  }
}

run "function_roles_are_fenced" {
  command = plan

  assert {
    condition = alltrue([
      for r in [aws_iam_role.runner, aws_iam_role.killswitch, aws_iam_role.intake] :
      r.path == "/calls-log/" && r.permissions_boundary == "arn:aws:iam::333596351215:policy/calls-log-workload-boundary"
    ])
    error_message = "Every function role sits under /calls-log/ with the bootstrap's boundary (the deploy role can create no other kind)."
  }

  assert {
    condition = length([
      for a in flatten([for s in jsondecode(data.aws_iam_policy_document.runner.json).Statement : s.Action]) :
      a if contains(["s3:DeleteObject", "dynamodb:PutItem", "sqs:SendMessage"], a) || endswith(a, ":*")
    ]) == 0
    error_message = "The runner may not delete objects, create uploads, enqueue, or hold whole-service rights."
  }

  assert {
    condition = (
      toset(flatten([
        for s in jsondecode(data.aws_iam_policy_document.runner.json).Statement : s.Resource
        if contains(flatten([s.Action]), "dynamodb:Query")
      ])) == toset(["arn:aws:dynamodb:ap-southeast-2:333596351215:table/calls-log-folders"]) &&
      !contains(flatten([for s in jsondecode(data.aws_iam_policy_document.runner.json).Statement : s.Action]), "dynamodb:Scan")
    )
    error_message = "The runner queries its day's uploads in the Folder Status table only (Story 4.6); it never scans."
  }

  assert {
    condition = toset(flatten([
      for s in jsondecode(data.aws_iam_policy_document.runner.json).Statement : s.Resource
      if contains(flatten([s.Action]), "s3:PutObject")
    ])) == toset(["arn:aws:s3:::calls-log-data-333596351215/work/*", "arn:aws:s3:::calls-log-data-333596351215/results/*"])
    error_message = "The runner writes checkpoints and Day Sheets only, never uploads."
  }

  assert {
    condition = toset(flatten([
      for s in jsondecode(data.aws_iam_policy_document.runner.json).Statement : s.Resource
      if contains(flatten([s.Action]), "s3:GetObject")
      ])) == toset([
      "arn:aws:s3:::calls-log-data-333596351215/inbox/*",
      "arn:aws:s3:::calls-log-data-333596351215/work/*",
      "arn:aws:s3:::calls-log-data-333596351215/results/*",
    ])
    error_message = "The runner reads uploads, checkpoints and, to re-send them (Story 3.8), Day Sheets; nothing else."
  }

  # A missing checkpoint must read as 404 (NoSuchKey), not 403: S3 gives 404
  # only to a caller allowed to list the bucket, and a GetObject request has
  # no s3:prefix for a condition to match.
  assert {
    condition = alltrue([
      for s in jsondecode(data.aws_iam_policy_document.runner.json).Statement :
      !can(s.Condition) && s.Resource == "arn:aws:s3:::calls-log-data-333596351215"
      if contains(flatten([s.Action]), "s3:ListBucket")
    ])
    error_message = "The runner's s3:ListBucket must cover the bucket without a prefix condition, or every new image's checkpoint read is AccessDenied."
  }
}

run "a_runaway_is_stopped_under_the_cap" {
  command = plan

  assert {
    condition     = local.runaway_worst_case_usd <= local.runaway_cap_usd && local.runaway_cap_usd == 1.05 && local.runaway_periods >= 2
    error_message = "The runaway alarm must fire before a runaway costs $1.05 past the free tier (the Owner's 2026-10-21 cap)."
  }

  assert {
    condition = alltrue([
      for a in [aws_cloudwatch_metric_alarm.dlq, aws_cloudwatch_metric_alarm.errors, aws_cloudwatch_metric_alarm.runaway] :
      a.alarm_actions == toset([
        "arn:aws:lambda:ap-southeast-2:333596351215:function:calls-log-killswitch",
        "arn:aws:sns:ap-southeast-2:333596351215:calls-log-alerts",
      ])
    ])
    error_message = "Every guardrail alarm calls the kill switch and emails the Owner."
  }

  assert {
    condition     = aws_cloudwatch_metric_alarm.runaway.evaluation_periods == 16 && aws_cloudwatch_metric_alarm.runaway.threshold == 2
    error_message = "At 2,048 MB: both slots busy for 16 quarter-hours (4 hours) in a row."
  }
}

run "bigger_memory_shortens_the_runaway_window" {
  command = plan

  variables {
    runner_memory_mb = 3008
  }

  assert {
    condition     = local.runaway_periods == 10 && local.runaway_worst_case_usd <= 1.05
    error_message = "At 3,008 MB the alarm must fire after 10 quarter-hours to stay under $1.05."
  }
}

run "the_bench_goes_away_when_disabled" {
  command = plan

  variables {
    enable_bench = false
  }

  assert {
    condition     = length(aws_lambda_function.bench) == 0 && length(aws_iam_role.bench) == 0
    error_message = "enable_bench = false must remove every benchmark resource."
  }
}

# CreateFunction needs the repository policy to let the Lambda service pull
# the image; the execution role's rights alone were refused (#16's first apply).
run "lambda_may_pull_the_image_and_nothing_more" {
  command = plan

  assert {
    condition     = aws_ecr_repository_policy.runner.repository == "calls-log-runner"
    error_message = "The runner repository carries the policy that lets Lambda create functions from it."
  }

  assert {
    condition = (
      length(jsondecode(data.aws_iam_policy_document.runner_repository.json).Statement) == 1 &&
      one(jsondecode(data.aws_iam_policy_document.runner_repository.json).Statement).Principal == { Service = "lambda.amazonaws.com" } &&
      toset(flatten([one(jsondecode(data.aws_iam_policy_document.runner_repository.json).Statement).Action])) == toset(["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"])
    )
    error_message = "Only the Lambda service, and only pulling: no push, no delete, no other principal."
  }
}
