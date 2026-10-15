# Bootstrap: applied by hand once from the Owner's PC (docs/AWS_SETUP.md).
# It creates what every later deploy needs before GitHub can deploy anything:
# the Terraform state bucket and GitHub's keyless (OIDC) way into AWS. Its own
# state stays local and gitignored, because it creates the state bucket.

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
