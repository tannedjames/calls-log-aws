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
