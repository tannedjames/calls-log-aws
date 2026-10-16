variable "region" {
  description = "The one AWS region everything lives in."
  type        = string
  default     = "ap-southeast-2"
}

variable "account_id" {
  description = "The only AWS account this stack may touch."
  type        = string
  default     = "333596351215"
}

variable "runner_image_tag" {
  description = "The runner image every function runs: the git SHA deploy.yml pushed to calls-log-runner."
  type        = string

  validation {
    condition     = can(regex("^[0-9a-f]{40}$", var.runner_image_tag))
    error_message = "Must be a full 40-character git SHA (ECR tags are immutable SHAs)."
  }
}

variable "runner_memory_mb" {
  description = "The runner's memory size, chosen by Story 3.1's measurement (lowest GB-s per image)."
  type        = number
  default     = 2048

  validation {
    condition     = var.runner_memory_mb >= 1536 && var.runner_memory_mb <= 10240
    error_message = "1,536 to 10,240 MB: below 1,536 MB the OCR runs out of memory."
  }
}

variable "enable_bench" {
  description = "Story 3.1: create the benchmark functions (one per memory size). False once measured."
  type        = bool
  default     = true
}
