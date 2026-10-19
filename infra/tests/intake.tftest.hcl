# Offline checks of the upload and status page (Epic 4, AD-17):
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

run "the_page_is_public_only_through_its_url" {
  command = plan

  assert {
    condition     = aws_lambda_function_url.intake.authorization_type == "NONE"
    error_message = "The Function URL is public (AD-17); the passphrase guards the API."
  }

  assert {
    condition = (
      aws_lambda_permission.intake_url.action == "lambda:InvokeFunctionUrl" &&
      aws_lambda_permission.intake_url.principal == "*" &&
      aws_lambda_permission.intake_url.function_url_auth_type == "NONE"
    )
    error_message = "AD-17's first statement: anyone may call the URL."
  }

  assert {
    condition = (
      aws_lambda_permission.intake_invoke.action == "lambda:InvokeFunction" &&
      aws_lambda_permission.intake_invoke.principal == "*" &&
      aws_lambda_permission.intake_invoke.invoked_via_function_url == true
    )
    error_message = "AD-17's second statement: InvokeFunction for anyone, but only through the URL; without it every call is 403."
  }

  assert {
    condition = (
      aws_lambda_function.intake.image_config[0].command == tolist(["/lambda-entrypoint.sh", "cloud.intake.handler"]) &&
      aws_lambda_function.intake.reserved_concurrent_executions == 2 &&
      aws_lambda_function.intake.timeout == 30 &&
      length(aws_lambda_function.intake.vpc_config) == 0
    )
    error_message = "The page runs cloud.intake.handler, two at a time at most, 30 s, no VPC."
  }
}

run "the_intake_role_is_fenced" {
  command = plan

  assert {
    condition = (
      aws_iam_role.intake.path == "/calls-log/" &&
      aws_iam_role.intake.permissions_boundary == "arn:aws:iam::333596351215:policy/calls-log-workload-boundary"
    )
    error_message = "The intake role sits under /calls-log/ with the bootstrap's boundary."
  }

  assert {
    condition = length([
      for a in flatten([for s in jsondecode(data.aws_iam_policy_document.intake.json).Statement : s.Action]) :
      a if endswith(a, ":*") || contains(["s3:DeleteObject", "dynamodb:Scan", "dynamodb:DeleteItem",
      "sqs:ReceiveMessage", "sqs:DeleteMessage", "sns:Publish"], a)
    ]) == 0
    error_message = "The page may not delete, scan, take queue messages, alert or hold whole-service rights."
  }

  assert {
    condition = toset(flatten([
      for s in jsondecode(data.aws_iam_policy_document.intake.json).Statement : s.Resource
      if contains(flatten([s.Action]), "s3:PutObject")
    ])) == toset(["arn:aws:s3:::calls-log-data-333596351215/inbox/*"])
    error_message = "Presigned uploads may write inbox/ only: never a Day Sheet or a checkpoint."
  }

  assert {
    condition = toset(flatten([
      for s in jsondecode(data.aws_iam_policy_document.intake.json).Statement : s.Resource
      if contains(flatten([s.Action]), "s3:GetObject")
    ])) == toset(["arn:aws:s3:::calls-log-data-333596351215/results/*"])
    error_message = "Presigned downloads may read Day Sheets only: never a screenshot."
  }

  assert {
    condition = one([
      for s in jsondecode(data.aws_iam_policy_document.intake.json).Statement : s.Condition
      if contains(flatten([s.Action]), "s3:ListBucket")
    ]) == { StringLike = { "s3:prefix" = ["inbox/*", "results/*"] } }
    error_message = "The page lists uploads and Day Sheets only."
  }

  assert {
    condition = toset(flatten([
      for s in jsondecode(data.aws_iam_policy_document.intake.json).Statement : s.Resource
      if contains(flatten([s.Action]), "dynamodb:Query")
      ])) == toset(["arn:aws:dynamodb:ap-southeast-2:333596351215:table/calls-log-folders",
    "arn:aws:dynamodb:ap-southeast-2:333596351215:table/calls-log-folders/index/uploads-by-day"])
    error_message = "The page queries the uploads-by-day index (Status tab) and the table (one Person-Day's uploads, for Re-run); it never scans."
  }

  assert {
    condition = one([
      for s in jsondecode(data.aws_iam_policy_document.intake.json).Statement : s.Condition
      if contains(flatten([s.Action]), "lambda:UpdateEventSourceMapping")
    ]) == { ArnLike = { "lambda:FunctionArn" = "arn:aws:lambda:ap-southeast-2:333596351215:function:calls-log-runner" } }
    error_message = "The Owner tab may stop or start the runner's queue trigger, and no other function's."
  }

  assert {
    condition = length([
      for a in flatten([for s in jsondecode(data.aws_iam_policy_document.intake.json).Statement : s.Action]) :
      a if startswith(a, "lambda:") && !contains(["lambda:UpdateEventSourceMapping", "lambda:GetEventSourceMapping",
      "lambda:ListEventSourceMappings"], a)
    ]) == 0
    error_message = "The page may not change any function: no concurrency, code or configuration rights."
  }
}

run "only_the_page_may_post_to_the_bucket" {
  command = plan

  override_resource {
    target          = aws_lambda_function_url.intake
    override_during = plan
    values          = { function_url = "https://abcdefghijklmnopqrstuvwxyz012345.lambda-url.ap-southeast-2.on.aws/" }
  }

  assert {
    condition = (
      length(aws_s3_bucket_cors_configuration.data.cors_rule) == 1 &&
      one(aws_s3_bucket_cors_configuration.data.cors_rule).allowed_methods == toset(["POST"]) &&
      one(aws_s3_bucket_cors_configuration.data.cors_rule).allowed_origins == toset(["https://abcdefghijklmnopqrstuvwxyz012345.lambda-url.ap-southeast-2.on.aws"])
    )
    error_message = "CORS allows POST from the page's own origin (no trailing slash), nothing else."
  }
}

run "a_flood_turns_the_page_off" {
  command = plan

  assert {
    condition = (
      aws_cloudwatch_metric_alarm.intake_runaway.alarm_name == "calls-log-intake-runaway" &&
      aws_cloudwatch_metric_alarm.intake_runaway.metric_name == "Invocations" &&
      aws_cloudwatch_metric_alarm.intake_runaway.dimensions.FunctionName == "calls-log-intake" &&
      aws_cloudwatch_metric_alarm.intake_runaway.period == 900 &&
      aws_cloudwatch_metric_alarm.intake_runaway.threshold == 600 &&
      contains(aws_cloudwatch_metric_alarm.intake_runaway.alarm_actions,
      "arn:aws:lambda:ap-southeast-2:333596351215:function:calls-log-killswitch")
    )
    error_message = "600 page requests in 15 minutes call the kill switch (its name is what cloud/killswitch.py dispatches on)."
  }

  assert {
    condition = (
      aws_cloudwatch_metric_alarm.intake_busy.alarm_name == "calls-log-intake-busy" &&
      aws_cloudwatch_metric_alarm.intake_busy.metric_name == "Duration" &&
      aws_cloudwatch_metric_alarm.intake_busy.statistic == "Sum" &&
      aws_cloudwatch_metric_alarm.intake_busy.threshold == 60000 &&
      contains(aws_cloudwatch_metric_alarm.intake_busy.alarm_actions,
      "arn:aws:lambda:ap-southeast-2:333596351215:function:calls-log-killswitch")
    )
    error_message = "60 s of page compute in 15 minutes (a slow passphrase guesser) also calls the kill switch."
  }

  assert {
    condition     = alltrue([for k in ["intake_runaway", "intake_busy"] : contains(keys(aws_lambda_permission.alarms), k)])
    error_message = "Both intake alarms may invoke the kill switch."
  }

  assert {
    condition = one([
      for s in jsondecode(data.aws_iam_policy_document.killswitch.json).Statement : s.Resource
      if contains(flatten([s.Action]), "lambda:PutFunctionConcurrency")
    ]) == "arn:aws:lambda:ap-southeast-2:333596351215:function:calls-log-intake"
    error_message = "The kill switch may throttle the upload page, and no other function."
  }
}

run "the_status_view_has_its_index" {
  command = plan

  assert {
    condition = one([
      for g in aws_dynamodb_table.folders.global_secondary_index :
      [for k in g.key_schema : "${k.key_type}:${k.attribute_name}"] if g.name == "uploads-by-day"
    ]) == ["HASH:upDay", "RANGE:sk"]
    error_message = "uploads-by-day is keyed by the upload's UTC date, then sk (cloud/status.py recent)."
  }
}
