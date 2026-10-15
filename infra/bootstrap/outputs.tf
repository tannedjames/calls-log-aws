output "state_bucket" {
  description = "Bucket for the infra/ stack's backend (key prefix prod/)."
  value       = aws_s3_bucket.state.bucket
}

output "deploy_role_arn" {
  description = "Stored in GitHub as the AWS_DEPLOY_ROLE_ARN variable (not a secret)."
  value       = aws_iam_role.deploy.arn
}

output "github_oidc_provider_arn" {
  value = aws_iam_openid_connect_provider.github.arn
}
