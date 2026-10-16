# Offline checks of the ECR settings the deploy relies on. The mock provider
# stands in for AWS, so this needs no credentials:
#   terraform -chdir=infra init -backend=false
#   terraform -chdir=infra test

mock_provider "aws" {
  # Policy documents must be valid JSON for the resources that use them; their
  # contents are checked in stores.tftest.hcl.
  mock_data "aws_iam_policy_document" {
    defaults = {
      json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}"
    }
  }
}

variables {
  runner_image_tag = "0123456789abcdef0123456789abcdef01234567"
}

run "runner_repository" {
  command = plan

  assert {
    condition     = aws_ecr_repository.runner.name == "calls-log-runner"
    error_message = "The runner repository must be named calls-log-runner (the deploy role covers calls-log-*)."
  }

  assert {
    condition     = aws_ecr_repository.runner.image_tag_mutability == "IMMUTABLE"
    error_message = "Image tags must be immutable, so a git SHA tag always names the code it was built from."
  }

  assert {
    condition     = aws_ecr_repository.runner.force_delete == false
    error_message = "Deleting the repository must never delete its images."
  }

  assert {
    condition     = aws_ecr_repository.runner.image_scanning_configuration[0].scan_on_push
    error_message = "Basic scanning on push must stay on (it is free)."
  }
}

run "keeps_only_the_newest_two_images" {
  command = plan

  assert {
    condition     = length(jsondecode(aws_ecr_lifecycle_policy.runner.policy).rules) == 1
    error_message = "The lifecycle policy must have exactly one rule."
  }

  assert {
    condition = jsondecode(aws_ecr_lifecycle_policy.runner.policy).rules[0].selection == {
      tagStatus   = "any"
      countType   = "imageCountMoreThan"
      countNumber = 2
    }
    error_message = "The lifecycle rule must keep only the newest 2 images, tagged or not."
  }

  assert {
    condition     = jsondecode(aws_ecr_lifecycle_policy.runner.policy).rules[0].action.type == "expire"
    error_message = "Images beyond the newest 2 must be expired."
  }
}
