# The account's default VPC + subnets — the same network the overview stack
# (cluster, datastore EC2) runs in, so the backend tasks can reach MariaDB/Redis.
data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

# CloudFront edge IP ranges — used to lock the ALB so only CloudFront reaches it.
data "aws_ec2_managed_prefix_list" "cloudfront" {
  name = "com.amazonaws.global.cloudfront.origin-facing"
}

# =====================================================================
# Shared resources created by the overview stack. Referenced here by their
# well-known names/tags (decoupled multi-repo pattern — no cross-stack
# remote_state). All of these must already exist; apply the overview stack
# first. Secrets (encryption_key, jwt_secret_key, etc.) only exist once
# their corresponding variables were set in the overview's tfvars.
# =====================================================================

# ECS cluster ("streamsight"), shared by all apps.
data "aws_ecs_cluster" "main" {
  cluster_name = var.project
}

# Per-app ECR repo + IAM roles the overview stack provisioned for the backend.
data "aws_ecr_repository" "backend" {
  name = "${var.project}-backend"
}

data "aws_iam_role" "execution" {
  # Reads /streamsight/shared/* + /streamsight/backend/* (least privilege).
  name = "${var.project}-backend-execution"
}

data "aws_iam_role" "task" {
  # Shared task role — what the running container may call.
  name = "${var.project}-ecs-task"
}

# ---- SSM Secrets (ARNs only; values stay in SSM) ----

data "aws_ssm_parameter" "db_password" {
  name = "/${var.project}/shared/db_password"
}

data "aws_ssm_parameter" "redis_password" {
  name = "/${var.project}/shared/redis_password"
}

data "aws_ssm_parameter" "encryption_key" {
  name = "/${var.project}/backend/encryption_key"
}

data "aws_ssm_parameter" "jwt_secret_key" {
  name = "/${var.project}/backend/jwt_secret_key"
}

data "aws_ssm_parameter" "refresh_token_hash_secret" {
  name = "/${var.project}/backend/refresh_token_hash_secret"
}

# Initial super-admin — only fetched when use_initial_admin = true.
# The overview stack must have set initial_admin_password_hash so all three
# SSM params exist; flip use_initial_admin to true after that apply.
data "aws_ssm_parameter" "initial_admin_password_hash" {
  count = var.use_initial_admin ? 1 : 0
  name  = "/${var.project}/backend/initial_admin_password_hash"
}

data "aws_ssm_parameter" "initial_admin_username" {
  count = var.use_initial_admin ? 1 : 0
  name  = "/${var.project}/backend/initial_admin_username"
}

data "aws_ssm_parameter" "initial_admin_name" {
  count = var.use_initial_admin ? 1 : 0
  name  = "/${var.project}/backend/initial_admin_name"
}

# Datastore EC2 (MariaDB + Redis + node-exporter + mysqld-exporter).
data "aws_instance" "datastore" {
  filter {
    name   = "tag:Name"
    values = ["${var.project}-datastore"]
  }
  filter {
    name   = "instance-state-name"
    values = ["running"]
  }
}

# The overview's ECS task security group. The datastore SG only accepts traffic
# from members of THIS group (ports 3306/6379/9100/9104), so the backend service
# joins it (in addition to its own SG) to reach MariaDB, Redis, and the
# monitoring exporters — no rule change needed on the overview stack.
data "aws_security_group" "shared_ecs" {
  filter {
    name   = "tag:Name"
    values = ["${var.project}-ecs"]
  }
  vpc_id = data.aws_vpc.default.id
}

# Private DNS namespace and inter-task SG provisioned by the overview stack.
# The backend registers itself here as backend.streamsight.local so the
# frontend BFF can reach it via VPC-internal DNS (no CloudFront round-trip).
data "aws_service_discovery_dns_namespace" "main" {
  name = "${var.project}.local"
  type = "DNS_PRIVATE"
}

data "aws_security_group" "internal" {
  filter {
    name   = "tag:Name"
    values = ["${var.project}-internal"]
  }
  vpc_id = data.aws_vpc.default.id
}
