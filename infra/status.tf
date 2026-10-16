# ---------------------------------------------------------------------------
# Folder Status (AD-4, AD-5): one item per upload and one lease item per
# Person-Day, written only by cloud/status.py with conditional writes.
#   pk = "pd#<Person-Day>"   sk = "up#<uploadId>" | "lease"
# Index uploads-by-day (upDay = the uploadId's UTC date, then sk) holds upload
# items only, so the upload page's status view queries a day, never scans.
# On demand and encrypted with DynamoDB's default key. No point-in-time
# recovery: the table can be rebuilt, and the Day Sheets, the record that
# matters, are versioned in S3 (spine, Deferred). Deletion protection on, and
# the deploy role can't delete tables either (infra/bootstrap).
# Cost: about 3,500 writes a month, doubled by the index, plus the upload
# page's status view (8 small queries a minute while it is open): about $0.02.
# ---------------------------------------------------------------------------

resource "aws_dynamodb_table" "folders" {
  name                        = local.names.table
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

  attribute {
    name = "upDay"
    type = "S"
  }

  global_secondary_index {
    name            = "uploads-by-day"
    projection_type = "ALL" # items are small; the page shows most attributes

    key_schema {
      attribute_name = "upDay"
      key_type       = "HASH"
    }

    key_schema {
      attribute_name = "sk"
      key_type       = "RANGE"
    }
  }
}
