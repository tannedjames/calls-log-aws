# Offline checks of the data bucket, the parameters and the budget (Story 2.4):
#   terraform -chdir=infra init -backend=false
#   terraform -chdir=infra test
#
# Plan only: nothing is created. The real provider builds the policy
# documents locally; fake credentials keep it from calling AWS. This provider
# block replaces infra/'s own, which would ask AWS for the account ID.

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

run "data_bucket_is_locked_down" {
  command = plan

  assert {
    condition     = aws_s3_bucket.data.bucket == "calls-log-data-333596351215"
    error_message = "The data bucket must be calls-log-data-<account id> (the deploy role's rights name it)."
  }

  assert {
    condition = alltrue([
      aws_s3_bucket_public_access_block.data.block_public_acls,
      aws_s3_bucket_public_access_block.data.block_public_policy,
      aws_s3_bucket_public_access_block.data.ignore_public_acls,
      aws_s3_bucket_public_access_block.data.restrict_public_buckets,
    ])
    error_message = "All four Block Public Access settings must be on."
  }

  assert {
    condition     = one(aws_s3_bucket_ownership_controls.data.rule).object_ownership == "BucketOwnerEnforced"
    error_message = "ACLs must be disabled (BucketOwnerEnforced), so no ACL can make an object public."
  }

  assert {
    condition = alltrue([
      for r in aws_s3_bucket_server_side_encryption_configuration.data.rule :
      one(r.apply_server_side_encryption_by_default).sse_algorithm == "AES256"
    ])
    error_message = "Objects must be encrypted with SSE-S3 (AES256): no customer-managed KMS key (AD-11)."
  }

  assert {
    condition     = one(aws_s3_bucket_versioning.data.versioning_configuration).status == "Enabled"
    error_message = "Versioning must be on: results/ keeps every Day Sheet version instead of (n) copies."
  }

  assert {
    condition = jsondecode(data.aws_iam_policy_document.data_tls_only.json).Statement == [{
      Sid       = "DenyNonTls"
      Effect    = "Deny"
      Action    = "s3:*"
      Principal = "*"
      Resource  = ["arn:aws:s3:::calls-log-data-333596351215/*", "arn:aws:s3:::calls-log-data-333596351215"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
    error_message = "The bucket policy must deny every request that isn't over TLS, on the bucket and its objects."
  }
}

run "only_uploads_checkpoints_and_lake_hand_overs_expire" {
  command = plan

  # Explicit indexes, not for-expressions: Terraform 1.16.5 crashes while
  # printing a failed for-expression over these rules.

  assert {
    condition     = local.expiring_prefixes == ["inbox/", "work/", "bench/", "staging/", "quarantine/"]
    error_message = "Only inbox/, work/, bench/, staging/ and quarantine/ expire after 30 days: Day Sheets in results/ and the lake/ are kept forever."
  }

  assert {
    condition = (
      aws_s3_bucket_lifecycle_configuration.data.rule[0].filter[0].prefix == "inbox/" &&
      aws_s3_bucket_lifecycle_configuration.data.rule[0].expiration[0].days == 30 &&
      aws_s3_bucket_lifecycle_configuration.data.rule[0].noncurrent_version_expiration[0].noncurrent_days == 1
    )
    error_message = "inbox/ must expire after 30 days, and its noncurrent versions 1 day later, or screenshots outlive 30 days."
  }

  assert {
    condition = (
      aws_s3_bucket_lifecycle_configuration.data.rule[1].filter[0].prefix == "work/" &&
      aws_s3_bucket_lifecycle_configuration.data.rule[1].expiration[0].days == 30 &&
      aws_s3_bucket_lifecycle_configuration.data.rule[1].noncurrent_version_expiration[0].noncurrent_days == 1
    )
    error_message = "work/ must expire after 30 days, and its noncurrent versions 1 day later."
  }

  assert {
    condition = (
      aws_s3_bucket_lifecycle_configuration.data.rule[2].filter[0].prefix == "bench/" &&
      aws_s3_bucket_lifecycle_configuration.data.rule[2].expiration[0].days == 30 &&
      aws_s3_bucket_lifecycle_configuration.data.rule[2].noncurrent_version_expiration[0].noncurrent_days == 1
    )
    error_message = "bench/ (Story 3.1's made-up images) must expire after 30 days, and its noncurrent versions 1 day later."
  }

  assert {
    condition = (
      aws_s3_bucket_lifecycle_configuration.data.rule[3].filter[0].prefix == "staging/" &&
      aws_s3_bucket_lifecycle_configuration.data.rule[3].expiration[0].days == 30 &&
      aws_s3_bucket_lifecycle_configuration.data.rule[3].noncurrent_version_expiration[0].noncurrent_days == 1
    )
    error_message = "staging/ (the data lake's hand-over, ADR 9) must expire after 30 days, and its noncurrent versions 1 day later."
  }

  assert {
    condition = (
      aws_s3_bucket_lifecycle_configuration.data.rule[4].filter[0].prefix == "quarantine/" &&
      aws_s3_bucket_lifecycle_configuration.data.rule[4].expiration[0].days == 30 &&
      aws_s3_bucket_lifecycle_configuration.data.rule[4].noncurrent_version_expiration[0].noncurrent_days == 1
    )
    error_message = "quarantine/ (envelopes that failed the lake's checks) must expire after 30 days, and its noncurrent versions 1 day later."
  }

  assert {
    condition = (
      aws_s3_bucket_lifecycle_configuration.data.rule[5].filter[0].prefix == "athena-results/" &&
      aws_s3_bucket_lifecycle_configuration.data.rule[5].expiration[0].days == 7 &&
      aws_s3_bucket_lifecycle_configuration.data.rule[5].noncurrent_version_expiration[0].noncurrent_days == 1
    )
    error_message = "athena-results/ must expire after 7 days, and its noncurrent versions 1 day later."
  }

  assert {
    condition = (
      contains(["", "-"], try(coalesce(aws_s3_bucket_lifecycle_configuration.data.rule[6].filter[0].prefix, "-"), "-")) &&
      coalesce(aws_s3_bucket_lifecycle_configuration.data.rule[6].expiration[0].days, 0) == 0 &&
      aws_s3_bucket_lifecycle_configuration.data.rule[6].expiration[0].expired_object_delete_marker &&
      length(aws_s3_bucket_lifecycle_configuration.data.rule[6].noncurrent_version_expiration) == 0
    )
    error_message = "The whole-bucket rule may only tidy delete markers and uploads: Day Sheets in results/ are kept forever."
  }
}

run "parameters_hold_only_placeholders" {
  command = plan

  assert {
    condition = toset(keys(aws_ssm_parameter.setting)) == toset([
      "webapp_url", "sync_secret", "sync_enabled", "name_prefixes", "intake_passphrase_hash",
      "owner_passphrase_hash", "alert_email", "lake_enabled", "person_salt",
    ])
    error_message = "The nine /calls-log/ parameters (the spine's six, the Owner tab's password and the data lake's two) must exist, and no others."
  }

  assert {
    condition = alltrue([
      for k, p in aws_ssm_parameter.setting :
      p.name == "/calls-log/${k}" && p.type == "SecureString" && p.value_wo_version == 1
    ])
    error_message = "Each parameter must be a SecureString named /calls-log/<name>, written once through value_wo."
  }

  assert {
    condition     = local.parameters["sync_enabled"] == "false"
    error_message = "The AWS Sync Switch must start off."
  }

  assert {
    condition     = local.parameters["lake_enabled"] == "false"
    error_message = "The data lake's switch must start off (ADR 9): nothing is staged until the Owner turns it on."
  }

  assert {
    condition     = alltrue([for k, v in local.parameters : contains(["unset", "false"], v)])
    error_message = "Terraform may only write placeholders; real values are set out of band."
  }
}

run "two_dollar_budget_alerts_the_topic" {
  command = plan

  # The topic's ARN is only known after apply; fixing it makes the budget's
  # notifications comparable at plan time.
  override_resource {
    target          = aws_sns_topic.alerts
    override_during = plan
    values          = { arn = "arn:aws:sns:ap-southeast-2:333596351215:calls-log-alerts" }
  }

  assert {
    condition = (
      aws_budgets_budget.monthly.budget_type == "COST" &&
      tonumber(aws_budgets_budget.monthly.limit_amount) == 2 &&
      aws_budgets_budget.monthly.limit_unit == "USD" &&
      aws_budgets_budget.monthly.time_unit == "MONTHLY"
    )
    error_message = "The budget must be $2.00 of cost per month."
  }

  assert {
    condition = toset([
      for n in aws_budgets_budget.monthly.notification : "${n.notification_type} ${n.threshold}"
      ]) == toset([
      "ACTUAL 50", "ACTUAL 80", "ACTUAL 100", "FORECASTED 50", "FORECASTED 80", "FORECASTED 100",
    ])
    error_message = "Actual and forecast cost must each alert at 50, 80 and 100%."
  }

  assert {
    condition = alltrue([
      for n in aws_budgets_budget.monthly.notification :
      length(n.subscriber_sns_topic_arns) == 1 && length(coalesce(n.subscriber_email_addresses, [])) == 0
    ])
    error_message = "Alerts go to the topic only; no email address may appear in Terraform."
  }

  assert {
    condition = jsondecode(data.aws_iam_policy_document.alerts_topic.json).Statement[0].Principal == {
      Service = "budgets.amazonaws.com"
    }
    error_message = "The topic policy must let Budgets publish."
  }
}

run "folder_status_table" {
  command = plan

  assert {
    condition = (
      aws_dynamodb_table.folders.name == "calls-log-folders" &&
      aws_dynamodb_table.folders.billing_mode == "PAY_PER_REQUEST" &&
      aws_dynamodb_table.folders.hash_key == "pk" &&
      aws_dynamodb_table.folders.range_key == "sk"
    )
    error_message = "calls-log-folders must be on demand, keyed pk/sk as cloud/status.py writes it."
  }

  assert {
    condition     = aws_dynamodb_table.folders.deletion_protection_enabled
    error_message = "Deletion protection must stay on."
  }
}
