output "cloudfront_url" {
  description = "Public HTTPS URL of the backend API — hit this from external clients or use as a fallback BACKEND_API_URL."
  value       = "https://${aws_cloudfront_distribution.backend.domain_name}"
}

output "alb_dns_name" {
  description = "ALB origin hostname. Direct access is blocked (403) — go through cloudfront_url."
  value       = aws_lb.backend.dns_name
}

output "ecs_cluster" {
  description = "Shared cluster the service runs in — set as ECS_CLUSTER in the deploy pipeline."
  value       = data.aws_ecs_cluster.main.cluster_name
}

output "ecs_service" {
  description = "Backend service name — set as ECS_SERVICE in the deploy pipeline."
  value       = aws_ecs_service.app.name
}

output "ecr_repository_url" {
  description = "ECR repo the pipeline pushes images to (created by the overview stack)."
  value       = data.aws_ecr_repository.backend.repository_url
}

output "task_family" {
  description = "Task definition family the pipeline registers new revisions under."
  value       = aws_ecs_task_definition.app.family
}

output "service_discovery_url" {
  description = "VPC-internal URL for the backend. Set BACKEND_API_URL in the frontend stack to this value — it is stable across CloudFront rebuilds and avoids egress costs."
  value       = "http://backend.${var.project}.local:${var.container_port}"
}
