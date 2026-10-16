# ---------------------------------------------------------------------------
# The runner image registry. deploy.yml pushes one image per merge, tagged
# with the full git SHA. Tags are immutable, so a tag always names the code it
# was built from. Only the newest 2 images are kept: the running one and the
# one before it, for a rollback.
# Cost: about 0.8 GB compressed per image, 2 kept, at $0.10 per GB-month: about
# $0.16 a month. Basic scanning on push is free.
# ---------------------------------------------------------------------------

resource "aws_ecr_repository" "runner" {
  name                 = "calls-log-runner"
  image_tag_mutability = "IMMUTABLE"
  # Deleting the repository must never take its images with it.
  force_delete = false

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }
}

resource "aws_ecr_lifecycle_policy" "runner" {
  repository = aws_ecr_repository.runner.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep only the newest 2 images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 2
      }
      action = { type = "expire" }
    }]
  })
}

# Lambda creates a function from this image only if the repository policy lets
# the Lambda service pull it (or if the caller may write that policy itself):
# the execution role's pull rights are not enough for CreateFunction. Found
# when #16's first apply failed with "Lambda does not have permission to
# access the ECR image". This is AWS's documented minimum statement. Another
# account's function still can't use the image: creating one cross-account
# also needs that account's principal granted here, and none is. Every
# function depends on this policy, so none is created before it exists.
data "aws_iam_policy_document" "runner_repository" {
  statement {
    sid     = "LambdaECRImageRetrievalPolicy"
    actions = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_ecr_repository_policy" "runner" {
  repository = aws_ecr_repository.runner.name
  policy     = data.aws_iam_policy_document.runner_repository.json
}
