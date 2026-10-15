variable "region" {
  description = "The one AWS region everything lives in."
  type        = string
  default     = "ap-southeast-2"
}

variable "github_repo" {
  description = "owner/name of the GitHub repo allowed to deploy, from its main branch only."
  type        = string
  default     = "tannedjames/calls-log-aws"
}

variable "github_subject_prefix" {
  description = <<-EOT
    Start of the subject GitHub puts in this repo's OIDC tokens. Repos created
    after 2026-07-15 get the immutable form repo:OWNER@OWNER-ID/REPO@REPO-ID,
    so a deleted and re-created repo of the same name can't deploy. Read it with
      gh api repos/OWNER/REPO/actions/oidc/customization/sub --jq .sub_claim_prefix
  EOT
  type        = string
  default     = "repo:tannedjames@185595532/calls-log-aws@1419265674"

  validation {
    condition     = can(regex("^repo:[^*?:]+$", var.github_subject_prefix))
    error_message = "Must look like repo:OWNER@ID/REPO@ID, with no wildcard or colon after repo:."
  }
}
