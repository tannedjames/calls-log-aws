# The prod stack. Applied only by GitHub Actions on main (deploy.yml), through
# the keyless calls-log-deploy role; pull requests only plan, with the
# read-only calls-log-plan role. The state bucket and both roles come from
# infra/bootstrap/, which a person applies by hand (docs/AWS_SETUP.md).

terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.56"
    }
  }

  # Backends can't read variables, so the values are written out. The lock is
  # a .tflock file next to the state (no DynamoDB table).
  backend "s3" {
    bucket       = "calls-log-tfstate-333596351215"
    key          = "prod/terraform.tfstate"
    region       = "ap-southeast-2"
    use_lockfile = true
    encrypt      = true
  }
}

provider "aws" {
  region = var.region
  # A plan or apply with credentials for any other account stops here.
  allowed_account_ids = [var.account_id]

  default_tags {
    tags = {
      project      = "calls-log"
      env          = "prod"
      "managed-by" = "terraform"
    }
  }
}
