# ---------------------------------------------------------------------------
# Folder Status (AD-4, AD-5): one item per upload and one lease item per
# Person-Day, written only by cloud/status.py with conditional writes.
#   pk = "pd#<Person-Day>"   sk = "up#<uploadId>" | "lease"
# On demand and encrypted with DynamoDB's default key. No point-in-time
# recovery: the table can be rebuilt, and the Day Sheets, the record that
# matters, are versioned in S3 (spine, Deferred). Deletion protection on, and
# the deploy role can't delete tables either (infra/bootstrap).
# Cost: about 3,500 writes and as many reads a month: about $0.01.
# ---------------------------------------------------------------------------

resource "aws_dynamodb_table" "folders" {
  name                        = "calls-log-folders"
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "pk"
  range_key                   = "sk"
  deletion_protection_enabled = true

  attribute {
    name = "pk"
    type = "S"
  }

  attribute {
    name = "sk"
    type = "S"
  }
}
