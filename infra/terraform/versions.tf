terraform {
  required_version = ">= 1.15"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # Remote state in S3 with native S3 state locking (use_lockfile, Terraform
  # 1.10+) — no DynamoDB table required. Bucket/key/region are supplied via
  # `-backend-config=backend.hcl` (see backend.hcl.example). Reuse the same
  # state bucket as the overview stack, but a DIFFERENT key so the two stacks
  # keep independent state.
  backend "s3" {
    use_lockfile = true
  }
}
