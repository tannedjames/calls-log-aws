output "state_bucket" {
  description = "Bucket for the infra/ stack's backend (key prefix prod/)."
  value       = aws_s3_bucket.state.bucket
}

output "deploy_role_arn" {
  description = "Stored in GitHub as the AWS_DEPLOY_ROLE_ARN variable (not a secret)."
  value       = aws_iam_role.deploy.arn
}

output "plan_role_arn" {
  description = "Stored in GitHub as the AWS_PLAN_ROLE_ARN variable (not a secret)."
  value       = aws_iam_role.plan.arn
}

output "github_oidc_provider_arn" {
  value = aws_iam_openid_connect_provider.github.arn
}

output "workload_boundary_arn" {
  description = "Permissions boundary every calls-log function role carries (infra/ refers to it by its fixed ARN)."
  value       = aws_iam_policy.workload_boundary.arn
}
