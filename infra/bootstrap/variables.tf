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
