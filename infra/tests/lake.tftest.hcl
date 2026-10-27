# Offline checks of the data lake (ADR 9, Phase 2): the publisher's fence,
# what S3 sends it, the Glue tables and the Athena workgroup.
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

run "the_publisher_is_a_small_zip_with_the_pandas_layer" {
  command = plan

  assert {
    condition = (
      aws_lambda_function.publisher.runtime == "python3.13" &&
      aws_lambda_function.publisher.handler == "cloud.publisher.handler" &&
      aws_lambda_function.publisher.package_type == "Zip" &&
      aws_lambda_function.publisher.architectures == tolist(["x86_64"]) &&
      aws_lambda_function.publisher.layers == tolist(["arn:aws:lambda:ap-southeast-2:336392948345:layer:AWSSDKPandas-Python313:16"]) &&
      aws_lambda_function.publisher.reserved_concurrent_executions == 2 &&
      aws_lambda_function.publisher.timeout == 60 &&
      length(aws_lambda_function.publisher.vpc_config) == 0
    )
    error_message = "The publisher is cloud.publisher.handler on python3.13 with the AWS SDK for pandas layer, two at a time, 60 s, no VPC."
  }

  assert {
    condition = (
      aws_iam_role.publisher.path == "/calls-log/" &&
      aws_iam_role.publisher.permissions_boundary == "arn:aws:iam::333596351215:policy/calls-log-workload-boundary"
    )
    error_message = "The publisher's role must sit under /calls-log/ with the workload boundary."
  }

  assert {
    condition     = toset(keys(aws_lambda_function.publisher.environment[0].variables)) == toset(["CALLS_LOG_BUCKET", "CALLS_LOG_TABLE"])
    error_message = "The publisher needs the bucket and the table, nothing else (no secret, no salt)."
  }

  assert {
    condition = (
      aws_lambda_function_event_invoke_config.publisher.maximum_retry_attempts == 2 &&
      aws_cloudwatch_log_group.publisher.retention_in_days == 30
    )
    error_message = "S3's calls are retried twice; logs are kept 30 days."
  }
}

run "the_publisher_reads_staging_and_writes_only_the_lake_and_quarantine" {
  command = plan

  assert {
    condition = toset(flatten([
      for s in jsondecode(data.aws_iam_policy_document.publisher.json).Statement : s.Action
    ])) == toset(["logs:CreateLogStream", "logs:PutLogEvents", "s3:GetObject", "s3:PutObject", "dynamodb:GetItem", "dynamodb:Query"])
    error_message = "The publisher may write its logs, read and write objects, and read Folder Status; nothing else (no Scan, no list, no SSM)."
  }

  assert {
    condition = toset(flatten([
      for s in jsondecode(data.aws_iam_policy_document.publisher.json).Statement : s.Resource
      if contains(flatten([s.Action]), "s3:GetObject")
    ])) == toset(["arn:aws:s3:::calls-log-data-333596351215/staging/*"])
    error_message = "The publisher reads staged envelopes only: never uploads, checkpoints or Day Sheets."
  }

  assert {
    condition = toset(flatten([
      for s in jsondecode(data.aws_iam_policy_document.publisher.json).Statement : s.Resource
      if contains(flatten([s.Action]), "s3:PutObject")
      ])) == toset([
      "arn:aws:s3:::calls-log-data-333596351215/lake/*",
      "arn:aws:s3:::calls-log-data-333596351215/quarantine/*",
    ])
    error_message = "The publisher writes the lake and the quarantine only."
  }

  assert {
    condition = toset(flatten([
      for s in jsondecode(data.aws_iam_policy_document.publisher.json).Statement : s.Resource
      if contains(flatten([s.Action]), "dynamodb:Query")
    ])) == toset(["arn:aws:dynamodb:ap-southeast-2:333596351215:table/calls-log-folders"])
    error_message = "The publisher reads the Folder Status table only, never an index."
  }

  assert {
    condition = (
      length(aws_s3_bucket_notification.data.lambda_function) == 1 &&
      aws_s3_bucket_notification.data.lambda_function[0].filter_prefix == "staging/" &&
      aws_s3_bucket_notification.data.lambda_function[0].filter_suffix == ".json" &&
      aws_s3_bucket_notification.data.lambda_function[0].events == toset(["s3:ObjectCreated:*"])
    )
    error_message = "S3 calls the publisher for new staging/*.json objects only."
  }

  assert {
    condition = (
      aws_lambda_permission.publisher_s3.principal == "s3.amazonaws.com" &&
      aws_lambda_permission.publisher_s3.source_arn == "arn:aws:s3:::calls-log-data-333596351215" &&
      aws_lambda_permission.publisher_s3.source_account == "333596351215"
    )
    error_message = "Only this account's data bucket may call the publisher."
  }
}

run "glue_tables_project_their_months_and_athena_is_capped" {
  command = plan

  assert {
    condition     = toset(keys(aws_glue_catalog_table.lake)) == toset(["calls", "labels", "sessions", "uploads"])
    error_message = "The lake has the contract's four tables."
  }

  assert {
    condition = alltrue([
      for name, t in aws_glue_catalog_table.lake :
      t.database_name == "calls_log" &&
      t.parameters["projection.enabled"] == "true" &&
      t.parameters["projection.work_month.format"] == "yyyy-MM" &&
      t.parameters["storage.location.template"] == "s3://calls-log-data-333596351215/lake/${name}/work_month=$${work_month}/" &&
      t.storage_descriptor[0].location == "s3://calls-log-data-333596351215/lake/${name}/" &&
      t.storage_descriptor[0].ser_de_info[0].serialization_library == "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe" &&
      length(t.partition_keys) == 1 && t.partition_keys[0].name == "work_month"
    ])
    error_message = "Each table reads Parquet under lake/<table>/ with work_month projected from the path (no crawler)."
  }

  assert {
    condition = (
      aws_athena_workgroup.lake.configuration[0].enforce_workgroup_configuration &&
      aws_athena_workgroup.lake.configuration[0].bytes_scanned_cutoff_per_query == 104857600 &&
      aws_athena_workgroup.lake.configuration[0].result_configuration[0].output_location == "s3://calls-log-data-333596351215/athena-results/" &&
      aws_athena_workgroup.lake.configuration[0].result_configuration[0].encryption_configuration[0].encryption_option == "SSE_S3"
    )
    error_message = "The workgroup enforces its settings: 100 MB per query, encrypted results in athena-results/."
  }

  assert {
    condition = (
      aws_cloudwatch_metric_alarm.lake_quarantined.alarm_actions == toset(["arn:aws:sns:ap-southeast-2:333596351215:calls-log-alerts"]) &&
      aws_cloudwatch_metric_alarm.publisher_errors.alarm_actions == toset(["arn:aws:sns:ap-southeast-2:333596351215:calls-log-alerts"]) &&
      aws_cloudwatch_metric_alarm.lake_quarantined.namespace == "calls-log/lake" &&
      aws_cloudwatch_metric_alarm.lake_quarantined.dimensions == tomap({ Function = "calls-log-publisher" })
    )
    error_message = "The lake's alarms email the Owner and never call the kill switch: the runner isn't at fault."
  }
}
