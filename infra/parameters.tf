# ---------------------------------------------------------------------------
# Run-time settings and secrets (AD-9), read by cloud/settings.py. Terraform
# only creates each parameter with a placeholder. The real values are set out
# of band with tools/put_parameters.py and never pass through Terraform.
#
# value_wo is write-only: Terraform writes the placeholder once and keeps
# value = null in its state, even after reading the parameter back. It writes
# again only if value_wo_version changes, so a real value set later is left
# alone. (ignore_changes = [value] would still copy the real value into the
# state on every refresh.)
#
# A refresh reads each parameter with decryption, so pull-request plans run
# with -refresh=false and the plan role has no SSM access (deploy.yml).
#
# Cost: standard parameters with the AWS-managed aws/ssm key: $0.
# ---------------------------------------------------------------------------

locals {
  # name => placeholder until the real value is set.
  parameters = {
    webapp_url             = "unset" # the Apps Script /exec URL
    sync_secret            = "unset" # the Web App's shared secret
    sync_enabled           = "false" # the AWS Sync Switch (AD-10): off until the Owner turns it on
    name_prefixes          = "unset" # JSON array of NAME_TO_ID keys, in Employees.gs order
    intake_passphrase_hash = "unset" # the upload page's passphrase hash (Story 4.1)
    owner_passphrase_hash  = "unset" # the page's owner password hash: its Owner tab
    alert_email            = "unset" # where alerts go; also subscribed to calls-log-alerts
  }
}

resource "aws_ssm_parameter" "setting" {
  for_each = local.parameters

  name             = "/calls-log/${each.key}"
  type             = "SecureString"
  value_wo         = each.value
  value_wo_version = 1
  description      = "calls-log setting; the real value is set out of band (tools/put_parameters.py)."
}
