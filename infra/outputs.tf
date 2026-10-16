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

output "runs_queue_url" {
  description = "Where a Ready Folder's message goes (tools/enqueue.py, the intake)."
  value       = aws_sqs_queue.runs.url
}

output "runner_trigger_uuid" {
  description = "The runner's queue trigger; the kill switch turns it off (Runbook: turning it back on)."
  value       = aws_lambda_event_source_mapping.runs.uuid
}

output "runaway_stop_worst_case_usd" {
  description = "The most a runaway could cost past the free tier before the kill switch stops it."
  value       = local.runaway_worst_case_usd
}

output "bench_functions" {
  description = "Story 3.1's benchmark functions, by memory size (empty once enable_bench is false)."
  value       = { for k, f in aws_lambda_function.bench : k => f.function_name }
}

output "intake_url" {
  description = "The upload and status page (Epic 4). Share it with the Operator, with the passphrase."
  value       = aws_lambda_function_url.intake.function_url
}
