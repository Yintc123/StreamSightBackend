provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project   = "StreamSight"
      App       = "backend"
      ManagedBy = "Terraform"
    }
  }
}
