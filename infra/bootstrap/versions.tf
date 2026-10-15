# Bootstrap: applied by hand from the Owner's PC (docs/AWS_SETUP.md), never by
# CI. It creates what every deploy needs before GitHub can deploy anything:
# the Terraform state bucket, GitHub's keyless (OIDC) way into AWS, and the two
# CI roles with their permissions. It is re-applied, after review, only when a
# story changes those permissions. Its own state stays local and gitignored,
# because it creates the state bucket.

terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.56"
    }
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      project      = "calls-log"
      env          = "prod"
      "managed-by" = "terraform"
    }
  }
}
