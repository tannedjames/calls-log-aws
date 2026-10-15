output "runner_repository_name" {
  value = aws_ecr_repository.runner.name
}

output "runner_repository_url" {
  description = "Where deploy.yml pushes the runner image: <url>:<git sha>."
  value       = aws_ecr_repository.runner.repository_url
}
