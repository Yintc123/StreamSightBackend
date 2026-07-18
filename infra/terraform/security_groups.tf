# Public ALB: HTTP only from CloudFront's edge network (not the open internet).
resource "aws_security_group" "alb" {
  name_prefix = "${var.project}-backend-alb-"
  description = "Backend ALB ingress"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description     = "HTTP from CloudFront edge only"
    from_port       = 80
    to_port         = 80
    protocol        = "tcp"
    prefix_list_ids = [data.aws_ec2_managed_prefix_list.cloudfront.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${var.project}-backend-alb" }
  lifecycle { create_before_destroy = true }
}

# Backend ECS tasks: only the backend ALB may reach the container port.
# Datastore access (MariaDB/Redis/exporters) comes from also attaching the
# shared ECS SG — see ecs.tf network_configuration.
resource "aws_security_group" "ecs" {
  name_prefix = "${var.project}-backend-ecs-"
  description = "Backend ECS service tasks"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description     = "From backend ALB"
    from_port       = var.container_port
    to_port         = var.container_port
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${var.project}-backend-ecs" }
  lifecycle { create_before_destroy = true }
}
