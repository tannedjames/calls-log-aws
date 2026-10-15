# ---------------------------------------------------------------------------
# Story 3.1: measure the runner image's cost per image in Lambda before the
# memory size is fixed. With enable_bench, one function per size runs the
# Synthetic Set uploaded to bench/ through the same per-image extraction as
# the runner (cloud/bench.py) and reports its timings; Lambda's REPORT line
# gives the billed GB-s and the cold start. Set enable_bench back to false
# afterwards and the pipeline removes all of this.
# Cost: a few hundred GB-s, inside the free tier.
# ---------------------------------------------------------------------------

locals {
  bench_sizes = var.enable_bench ? toset(["1536", "2048", "3008"]) : toset([])
}

resource "aws_iam_role" "bench" {
  count                = var.enable_bench ? 1 : 0
  name                 = "calls-log-bench"
  path                 = "/calls-log/"
  permissions_boundary = local.boundary_arn
  assume_role_policy   = data.aws_iam_policy_document.lambda_trust.json
}

data "aws_iam_policy_document" "bench" {
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = [for g in aws_cloudwatch_log_group.bench : "${g.arn}:*"]
  }

  statement {
    sid       = "ListBenchImages"
    actions   = ["s3:ListBucket"]
    resources = [local.data_bucket_arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["bench/*"]
    }
  }

  statement {
    sid       = "ReadBenchImages"
    actions   = ["s3:GetObject"]
    resources = ["${local.data_bucket_arn}/bench/*"]
  }

  statement {
    sid       = "Image"
    actions   = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"]
    resources = [local.arns.repository]
  }
}

resource "aws_iam_role_policy" "bench" {
  count  = var.enable_bench ? 1 : 0
  name   = "bench"
  role   = aws_iam_role.bench[0].id
  policy = data.aws_iam_policy_document.bench.json
}

resource "aws_cloudwatch_log_group" "bench" {
  for_each          = local.bench_sizes
  name              = "/aws/lambda/calls-log-bench-${each.key}"
  retention_in_days = 30
}

resource "aws_lambda_function" "bench" {
  for_each      = local.bench_sizes
  function_name = "calls-log-bench-${each.key}"
  role          = aws_iam_role.bench[0].arn
  package_type  = "Image"
  image_uri     = local.image
  architectures = ["x86_64"]
  memory_size   = tonumber(each.key)
  timeout       = 900

  image_config {
    command = ["/lambda-entrypoint.sh", "cloud.bench.handler"]
  }

  ephemeral_storage {
    size = 1024
  }

  environment {
    variables = { CALLS_LOG_BUCKET = aws_s3_bucket.data.bucket }
  }

  depends_on = [aws_cloudwatch_log_group.bench, aws_iam_role_policy.bench]
}
