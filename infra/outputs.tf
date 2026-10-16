output "runner_repository_name" {
  value = aws_ecr_repository.runner.name
}

output "runner_repository_url" {
  description = "Where deploy.yml pushes the runner image: <url>:<git sha>."
  value       = aws_ecr_repository.runner.repository_url
}

output "data_bucket" {
  description = "inbox/, work/ and results/ (infra/data.tf)."
  value       = aws_s3_bucket.data.bucket
}

output "alerts_topic_arn" {
  description = "Subscribe the Owner's email here with tools/put_parameters.py --alert-email."
  value       = aws_sns_topic.alerts.arn
}

output "folders_table" {
  description = "Folder Status (cloud/status.py)."
  value       = aws_dynamodb_table.folders.name
}
