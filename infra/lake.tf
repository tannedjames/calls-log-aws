# ---------------------------------------------------------------------------
# The data lake (ADR 9, Phase 2; docs/aws/data-contract.md).
#
#   staging/<Person-Day>/<uploadId>.json   the runner's hand-over (runner.tf)
#     -> calls-log-publisher                zip Lambda, called by S3 for each one
#        lake/<table>/work_month=<yyyy-mm>/<person_key>_<date>.parquet
#                                           the day's choice, checked; permanent
#        quarantine/<Person-Day>/<uploadId>.json
#                                           failed a check; 30 days, and an email
#   Glue database calls_log                 tables calls, sessions, uploads,
#                                           partition projection on work_month
#   Athena workgroup calls-log              100 MB per query; results 7 days
#
# The publisher's role sits under /calls-log/ with the bootstrap's boundary,
# which already allows what it needs; it never names a person (person_key is
# a salted HMAC). Its metrics are Embedded Metric Format log lines, so it
# needs no CloudWatch permission.
# Cost: an envelope takes about a second at 512 MB, far inside the free tier;
# the Glue catalog is free under a million objects; Athena is $5 per TB
# scanned, and a day's three files are a few KB; the alarms are within the
# free ten. All under the $2 budget, which covers every service.
# ---------------------------------------------------------------------------

locals {
  lake_database  = "calls_log"
  lake_workgroup = "calls-log"
  # AWS SDK for pandas 3.17.1, the AWS-managed layer: it brings pyarrow.
  pandas_layer_arn = "arn:aws:lambda:${var.region}:336392948345:layer:AWSSDKPandas-Python313:16"
  # Everything cloud/publisher.py imports, standard library apart.
  publisher_files = [
    "cloud/__init__.py", "cloud/lake.py", "cloud/naming.py", "cloud/publisher.py",
    "cloud/settings.py", "cloud/status.py", "config.py", "runtime_env.py",
  ]
  # The lake's columns and their types, in file order: cloud/lake.py
  # COLUMN_TYPES, which tests.py compares with this map.
  lake_columns = {
    calls = [
      ["person_key", "string"], ["work_date", "date"], ["upload_id", "string"], ["image_key", "string"],
      ["call_seq", "int"], ["start_ts", "timestamp"], ["end_ts", "timestamp"], ["duration_s", "int"],
      ["direction", "string"], ["call_status", "string"], ["countable", "boolean"], ["row_conf", "double"],
      ["start_conf", "double"], ["duration_conf", "double"], ["flagged", "boolean"], ["flag_field", "string"],
      ["flag_reason", "string"], ["parser_version", "string"], ["ocr_model", "string"],
      ["ingested_at", "timestamp"],
    ]
    sessions = [
      ["person_key", "string"], ["work_date", "date"], ["upload_id", "string"], ["session_no", "int"],
      ["start_ts", "timestamp"], ["end_ts", "timestamp"], ["span_min", "int"], ["call_count", "int"],
      ["source", "string"], ["ingested_at", "timestamp"],
    ]
    uploads = [
      ["upload_id", "string"], ["person_key", "string"], ["work_date", "date"], ["image_count", "int"],
      ["attempts", "int"], ["ready_at", "timestamp"], ["started_at", "timestamp"], ["finished_at", "timestamp"],
      ["status", "string"], ["runner_memory_mb", "int"],
    ]
  }
}

# ----- the publisher -------------------------------------------------------------

data "archive_file" "publisher" {
  type        = "zip"
  output_path = "${path.module}/build/publisher.zip"

  dynamic "source" {
    for_each = toset(local.publisher_files)

    content {
      content  = file("${path.module}/../${source.value}")
      filename = source.value
    }
  }
}

resource "aws_iam_role" "publisher" {
  name                 = local.names.publisher
  path                 = "/calls-log/"
  permissions_boundary = local.boundary_arn
  assume_role_policy   = data.aws_iam_policy_document.lambda_trust.json
}

data "aws_iam_policy_document" "publisher" {
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${local.arns.publisher_logs}:*"]
  }

  statement {
    sid       = "ReadStagedRecords"
    actions   = ["s3:GetObject"]
    resources = ["${local.data_bucket_arn}/staging/*"]
  }

  statement {
    sid       = "WriteTheLakeAndQuarantine"
    actions   = ["s3:PutObject"]
    resources = ["${local.data_bucket_arn}/lake/*", "${local.data_bucket_arn}/quarantine/*"]
  }

  # The day's choice: the upload's item, and its day's other uploads.
  statement {
    sid       = "FolderStatus"
    actions   = ["dynamodb:GetItem", "dynamodb:Query"]
    resources = [local.arns.table]
  }
}

resource "aws_iam_role_policy" "publisher" {
  name   = "publisher"
  role   = aws_iam_role.publisher.id
  policy = data.aws_iam_policy_document.publisher.json
}

resource "aws_cloudwatch_log_group" "publisher" {
  name              = "/aws/lambda/${local.names.publisher}"
  retention_in_days = 30
}

resource "aws_lambda_function" "publisher" {
  function_name    = local.names.publisher
  role             = aws_iam_role.publisher.arn
  runtime          = "python3.13"
  handler          = "cloud.publisher.handler"
  filename         = data.archive_file.publisher.output_path
  source_code_hash = data.archive_file.publisher.output_base64sha256
  architectures    = ["x86_64"]
  layers           = [local.pandas_layer_arn]
  memory_size      = 512
  timeout          = 60

  reserved_concurrent_executions = 2

  environment {
    variables = {
      CALLS_LOG_BUCKET = aws_s3_bucket.data.bucket
      CALLS_LOG_TABLE  = aws_dynamodb_table.folders.name
    }
  }

  depends_on = [aws_cloudwatch_log_group.publisher, aws_iam_role_policy.publisher]
}

# S3 calls it asynchronously: a failed envelope is tried twice more, then the
# errors alarm has emailed and a replay (docs/TECHNICAL.md) sends it again.
resource "aws_lambda_function_event_invoke_config" "publisher" {
  function_name                = aws_lambda_function.publisher.function_name
  maximum_retry_attempts       = 2
  maximum_event_age_in_seconds = 3600
}

resource "aws_lambda_permission" "publisher_s3" {
  statement_id   = "staged-records"
  action         = "lambda:InvokeFunction"
  function_name  = aws_lambda_function.publisher.function_name
  principal      = "s3.amazonaws.com"
  source_arn     = local.data_bucket_arn
  source_account = var.account_id
}

# The bucket's only notification: new envelopes in staging/.
resource "aws_s3_bucket_notification" "data" {
  bucket = aws_s3_bucket.data.id

  lambda_function {
    lambda_function_arn = aws_lambda_function.publisher.arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = "staging/"
    filter_suffix       = ".json"
  }

  depends_on = [aws_lambda_permission.publisher_s3]
}

# ----- the catalog and the workgroup ---------------------------------------------

resource "aws_glue_catalog_database" "lake" {
  name        = local.lake_database
  description = "calls-log data lake (ADR 9): calls, sessions and uploads, no names. docs/aws/data-contract.md"
}

# Partition projection: Athena works out the work_month partitions from the
# path, so there is no crawler and nothing to add when a month begins.
resource "aws_glue_catalog_table" "lake" {
  for_each = local.lake_columns

  name          = each.key
  database_name = aws_glue_catalog_database.lake.name
  table_type    = "EXTERNAL_TABLE"

  parameters = {
    EXTERNAL                              = "TRUE"
    classification                        = "parquet"
    "parquet.compression"                 = "SNAPPY"
    "projection.enabled"                  = "true"
    "projection.work_month.type"          = "date"
    "projection.work_month.format"        = "yyyy-MM"
    "projection.work_month.range"         = "2026-01,NOW"
    "projection.work_month.interval"      = "1"
    "projection.work_month.interval.unit" = "MONTHS"
    "storage.location.template"           = "s3://${local.data_bucket}/lake/${each.key}/work_month=$${work_month}/"
  }

  partition_keys {
    name = "work_month"
    type = "string"
  }

  storage_descriptor {
    location      = "s3://${local.data_bucket}/lake/${each.key}/"
    input_format  = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"

    ser_de_info {
      serialization_library = "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
      parameters            = { "serialization.format" = "1" }
    }

    dynamic "columns" {
      for_each = each.value

      content {
        name = columns.value[0]
        type = columns.value[1]
      }
    }
  }
}

resource "aws_athena_workgroup" "lake" {
  name        = local.lake_workgroup
  description = "Queries of the calls-log data lake (database calls_log)."

  configuration {
    enforce_workgroup_configuration    = true
    publish_cloudwatch_metrics_enabled = true
    bytes_scanned_cutoff_per_query     = 104857600 # 100 MB: a year of the lake is a few MB

    result_configuration {
      output_location = "s3://${local.data_bucket}/athena-results/"

      encryption_configuration {
        encryption_option = "SSE_S3"
      }
    }
  }
}

# ----- the alarms: email only, never the kill switch -----------------------------

resource "aws_cloudwatch_metric_alarm" "lake_quarantined" {
  alarm_name          = "calls-log-lake-quarantined"
  alarm_description   = "A day's records failed the data lake's checks and went to quarantine/ (docs/TECHNICAL.md, Runbook). The Day Sheet and the Sheet are not affected."
  namespace           = "calls-log/lake"
  metric_name         = "Quarantined"
  dimensions          = { Function = local.names.publisher } # cloud/publisher.py FUNCTION
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [local.alerts_topic_arn]
  depends_on          = [aws_sns_topic_policy.alerts]
}

resource "aws_cloudwatch_metric_alarm" "publisher_errors" {
  alarm_name          = "calls-log-publisher-errors"
  alarm_description   = "The data lake's publisher failed on an envelope (S3 retries it twice). The Day Sheet and the Sheet are not affected; see its log, then replay staging/ (docs/TECHNICAL.md, Runbook)."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = aws_lambda_function.publisher.function_name }
  statistic           = "Sum"
  period              = 900
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [local.alerts_topic_arn]
  depends_on          = [aws_sns_topic_policy.alerts]
}
