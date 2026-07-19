locals {
  app = "${var.project}-backend"
}

resource "aws_cloudwatch_log_group" "app" {
  name              = "/ecs/${local.app}"
  retention_in_days = 7
}

resource "aws_ecs_task_definition" "app" {
  family                   = local.app
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.task_cpu
  memory                   = var.task_memory
  execution_role_arn       = data.aws_iam_role.execution.arn
  task_role_arn            = data.aws_iam_role.task.arn

  runtime_platform {
    cpu_architecture        = "X86_64"
    operating_system_family = "LINUX"
  }

  container_definitions = jsonencode([{
    name      = local.app
    image     = "${data.aws_ecr_repository.backend.repository_url}:${var.image_tag}"
    essential = true

    portMappings = [{
      containerPort = var.container_port
      protocol      = "tcp"
    }]

    # Non-secret config. Secrets (DB_PASSWORD, REDIS_PASSWORD, ENCRYPTION_KEY,
    # JWT_SECRET_KEY, REFRESH_TOKEN_HASH_SECRET) come from SSM below.
    environment = [
      { name = "APP_ENV", value = "production" },
      { name = "PORT",    value = tostring(var.container_port) },

      { name = "DB_HOST", value = data.aws_instance.datastore.private_ip },
      { name = "DB_PORT", value = "3306" },
      { name = "DB_USER", value = var.db_user },
      { name = "DB_NAME", value = var.db_name },

      { name = "REDIS_HOST", value = data.aws_instance.datastore.private_ip },
      { name = "REDIS_PORT", value = "6379" },
      { name = "REDIS_DB",   value = tostring(var.redis_db) },

      # Reach the exporters on the datastore EC2 via its private IP, overriding
      # the docker-compose service-name defaults (node-exporter / mysqld-exporter).
      {
        name  = "MONITORING_INFRA_NODE_EXPORTER_URL"
        value = "http://${data.aws_instance.datastore.private_ip}:9100"
      },
      {
        name  = "MONITORING_INFRA_MYSQLD_EXPORTER_URL"
        value = "http://${data.aws_instance.datastore.private_ip}:9104"
      },

      # JSON-encoded list for pydantic-settings. Set to the frontend CloudFront
      # URL(s) so only legitimate browser origins can upgrade WebSocket connections.
      { name = "WS_ALLOWED_ORIGINS", value = jsonencode(var.ws_allowed_origins) },
    ]

    secrets = concat(
      [
        { name = "DB_PASSWORD",              valueFrom = data.aws_ssm_parameter.db_password.arn },
        { name = "REDIS_PASSWORD",           valueFrom = data.aws_ssm_parameter.redis_password.arn },
        { name = "ENCRYPTION_KEY",           valueFrom = data.aws_ssm_parameter.encryption_key.arn },
        { name = "JWT_SECRET_KEY",           valueFrom = data.aws_ssm_parameter.jwt_secret_key.arn },
        { name = "REFRESH_TOKEN_HASH_SECRET", valueFrom = data.aws_ssm_parameter.refresh_token_hash_secret.arn },
      ],
      var.use_initial_admin ? [
        { name = "INITIAL_ADMIN_PASSWORD", valueFrom = data.aws_ssm_parameter.initial_admin_password[0].arn },
        { name = "INITIAL_ADMIN_USERNAME",       valueFrom = data.aws_ssm_parameter.initial_admin_username[0].arn },
        { name = "INITIAL_ADMIN_NAME",           valueFrom = data.aws_ssm_parameter.initial_admin_name[0].arn },
      ] : [],
    )

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.app.name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "app"
      }
    }
  }])
}

# =====================================================================
# Service discovery registration
#
# Registers the backend under backend.streamsight.local so the frontend
# BFF can reach the FastAPI server via VPC-internal DNS instead of the
# public CloudFront URL (which changes on every rebuild and incurs egress
# cost). Frontend sets BACKEND_API_URL=http://backend.streamsight.local:8000.
#
# NOTE: Adding service_registries to an existing aws_ecs_service is a
# force-new in Terraform — the service will be briefly replaced on first apply.
# =====================================================================
resource "aws_service_discovery_service" "backend" {
  name = "backend"

  dns_config {
    namespace_id = data.aws_service_discovery_dns_namespace.main.id
    dns_records {
      ttl  = 10
      type = "A"
    }
    routing_policy = "MULTIVALUE"
  }

  # ECS controls registration; Cloud Map does not probe health independently.
  health_check_custom_config {
    failure_threshold = 1
  }
}

resource "aws_ecs_service" "app" {
  name            = local.app
  cluster         = data.aws_ecs_cluster.main.arn
  task_definition = aws_ecs_task_definition.app.arn
  desired_count   = var.desired_count

  # Fargate Spot (~70% cheaper). A reclaimed task is rescheduled with a short
  # gap; fine for a stateless API. Switch to FARGATE for zero interruption.
  capacity_provider_strategy {
    capacity_provider = "FARGATE_SPOT"
    weight            = 1
  }

  network_configuration {
    subnets = data.aws_subnets.default.ids
    # Own SG (ALB → 8000) + shared ECS SG (datastore: 3306/6379/9100/9104)
    # + internal SG (Cloud Map: VPC-internal ECS-to-ECS traffic).
    security_groups  = [aws_security_group.ecs.id, data.aws_security_group.shared_ecs.id, data.aws_security_group.internal.id]
    assign_public_ip = true # required to pull from ECR in the default VPC (no NAT)
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.backend.arn
    container_name   = local.app
    container_port   = var.container_port
  }

  service_registries {
    registry_arn = aws_service_discovery_service.backend.arn
  }

  health_check_grace_period_seconds = 60

  depends_on = [aws_lb_listener.http]

  # The app pipeline updates task_definition (new image) and may scale
  # desired_count; don't let `terraform apply` revert those.
  lifecycle {
    ignore_changes = [task_definition, desired_count]
  }
}
