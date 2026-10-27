# ---------------------------------------------------------------------------
# The data bucket (AD-8, AD-18). Its prefixes:
#   inbox/<uploadId>/   uploaded screenshots      kept 30 days
#   work/<uploadId>/    per-image checkpoints     kept 30 days
#   results/            Day Sheets, every version kept, never expires
#   staging/            a Done day's records for the data lake (ADR 9),
#                       written only while /calls-log/lake_enabled is on;
#                       kept 30 days
#   lake/               the data lake's Parquet (lake.tf), never expires
#   quarantine/         records that failed the lake's checks; kept 30 days
#   athena-results/     Athena query results; kept 7 days
# Never public, TLS only, encrypted, ACLs off. Versioned, so a Day Sheet that
# is processed again keeps its earlier versions instead of (n) copies.
# Cost: about 2.6 GB of 30-day screenshots at $0.025 per GB-month plus about
# 9,000 uploads a month: about $0.10 a month. Day Sheets are a few MB a year.
# ---------------------------------------------------------------------------

locals {
  data_bucket     = "calls-log-data-${var.account_id}"
  data_bucket_arn = "arn:aws:s3:::${local.data_bucket}"
  # Uploads, checkpoints, Story 3.1's benchmark images, the data lake's
  # hand-over and its quarantine expire after 30 days; results/ and lake/
  # have no rule, so nothing in them does. New prefixes go last so the other
  # rules keep their places.
  expiring_prefixes = ["inbox/", "work/", "bench/", "staging/", "quarantine/"]
}

resource "aws_s3_bucket" "data" {
  bucket = local.data_bucket

  # The Day Sheets in results/ must never be lost (FR-12); the deploy role
  # can't delete a bucket either (infra/bootstrap).
  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_public_access_block" "data" {
  bucket = aws_s3_bucket.data.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ACLs disabled: the bucket owns every object, so no request can make one
# public with an ACL.
resource "aws_s3_bucket_ownership_controls" "data" {
  bucket = aws_s3_bucket.data.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "data" {
  bucket = aws_s3_bucket.data.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_versioning" "data" {
  bucket = aws_s3_bucket.data.id

  versioning_configuration {
    status = "Enabled"
  }
}

data "aws_iam_policy_document" "data_tls_only" {
  statement {
    sid       = "DenyNonTls"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [local.data_bucket_arn, "${local.data_bucket_arn}/*"]

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

resource "aws_s3_bucket_policy" "data" {
  bucket = aws_s3_bucket.data.id
  policy = data.aws_iam_policy_document.data_tls_only.json

  depends_on = [aws_s3_bucket_public_access_block.data]
}

resource "aws_s3_bucket_lifecycle_configuration" "data" {
  bucket = aws_s3_bucket.data.id

  # With versioning on, expiring an object only hides it behind a delete
  # marker; the noncurrent rule then removes the data itself a day later, so
  # screenshots are really gone after 31 days.
  dynamic "rule" {
    for_each = local.expiring_prefixes

    content {
      id     = "expire-${trimsuffix(rule.value, "/")}-30-days"
      status = "Enabled"

      filter {
        prefix = rule.value
      }

      expiration {
        days = 30
      }

      noncurrent_version_expiration {
        noncurrent_days = 1
      }
    }
  }

  # Athena's query results (lake.tf): kept a week, long enough to download.
  rule {
    id     = "expire-athena-results-7-days"
    status = "Enabled"

    filter {
      prefix = "athena-results/"
    }

    expiration {
      days = 7
    }

    noncurrent_version_expiration {
      noncurrent_days = 1
    }
  }

  # The lake (lake.tf) keeps every day's current files forever. A Re-run or a
  # replay overwrites them, and versioning would keep each old file for good;
  # an old version goes after 30 days, time to undo a bad Re-run.
  rule {
    id     = "lake-old-versions-30-days"
    status = "Enabled"

    filter {
      prefix = "lake/"
    }

    noncurrent_version_expiration {
      noncurrent_days = 30
    }
  }

  rule {
    id     = "tidy-whole-bucket"
    status = "Enabled"

    filter {}

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }

    # Delete markers left once every version under them has expired.
    expiration {
      expired_object_delete_marker = true
    }
  }

  depends_on = [aws_s3_bucket_versioning.data]
}

# The upload page (intake.tf) posts images straight to the bucket from the
# browser: CORS allows exactly that, from the page's own origin only. Day
# Sheet downloads are plain links and need no CORS.
resource "aws_s3_bucket_cors_configuration" "data" {
  bucket = aws_s3_bucket.data.id

  cors_rule {
    allowed_methods = ["POST"]
    allowed_origins = [trimsuffix(aws_lambda_function_url.intake.function_url, "/")]
    max_age_seconds = 3000
  }
}
