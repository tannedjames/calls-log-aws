# ---------------------------------------------------------------------------
# The data bucket (AD-8, AD-18). Three prefixes:
#   inbox/<uploadId>/   uploaded screenshots      kept 30 days
#   work/<uploadId>/    per-image checkpoints     kept 30 days
#   results/            Day Sheets, every version kept, never expires
# Never public, TLS only, encrypted, ACLs off. Versioned, so a Day Sheet that
# is processed again keeps its earlier versions instead of (n) copies.
# Cost: about 2.6 GB of 30-day screenshots at $0.025 per GB-month plus about
# 9,000 uploads a month: about $0.10 a month. Day Sheets are a few MB a year.
# ---------------------------------------------------------------------------

locals {
  data_bucket     = "calls-log-data-${var.account_id}"
  data_bucket_arn = "arn:aws:s3:::${local.data_bucket}"
  # Uploads and checkpoints expire; results/ has no rule, so nothing in it does.
  expiring_prefixes = ["inbox/", "work/"]
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
