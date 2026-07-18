# =====================================================================
# The backend stack completes the "per-app" model started in the overview
# repo's Terraform: that stack creates the shared foundation (ECS cluster,
# datastore EC2 with MariaDB/Redis, SSM secrets) and, for the backend, an
# ECR repo + OIDC deploy role + execution role. This stack adds the backend's
# public path (CloudFront -> ALB -> Fargate) and registers the service in
# the VPC-internal Cloud Map namespace so the frontend BFF can reach it via
# backend.streamsight.local without going through CloudFront.
# Keep `region` and `project` identical to the overview stack.
# =====================================================================

variable "region" {
  description = "AWS region. MUST match the overview stack — this stack reuses its VPC, cluster and datastore."
  type        = string
  default     = "ap-northeast-2"
}

variable "project" {
  description = "Name prefix shared with the overview stack. Used to look up shared resources (cluster \"streamsight\", ECR \"streamsight-backend\", SSM \"/streamsight/...\")."
  type        = string
  default     = "streamsight"
}

variable "container_port" {
  description = "Port the FastAPI/Uvicorn server listens on."
  type        = number
  default     = 8000
}

variable "cloudfront_price_class" {
  description = "CloudFront edge coverage. PriceClass_200 includes Asia; _100 is US/EU only (cheapest)."
  type        = string
  default     = "PriceClass_200"
}

variable "image_tag" {
  description = "ECR image tag used at bootstrap. The app pipeline overrides this on each deploy."
  type        = string
  default     = "latest"
}

# ---- ECS service sizing ----

variable "desired_count" {
  type    = number
  default = 1
}

variable "task_cpu" {
  type    = number
  default = 256
}

variable "task_memory" {
  type    = number
  default = 512
}

# ---- Backend app config (non-secret; secrets come from SSM) ----

variable "db_user" {
  description = "DB user the backend connects with. MUST match the value used when the MariaDB user was created in the overview stack."
  type        = string
  default     = "streamsight"
}

variable "db_name" {
  description = "MariaDB database name. MUST match the overview stack."
  type        = string
  default     = "streamsight"
}

variable "redis_db" {
  description = "Redis logical DB number (0-15). Backend shares Redis with the Go server; they coexist on DB 0 via distinct key patterns in code."
  type        = number
  default     = 0
}

variable "ws_allowed_origins" {
  description = "WebSocket handshake allowed origins (防 CSWSH). Set to the frontend CloudFront URL(s) in production, e.g. [\"https://xxx.cloudfront.net\"]. Empty = no origin restriction (not recommended in production)."
  type        = list(string)
  default     = []
}

variable "use_initial_admin" {
  description = "Inject initial super-admin credentials from SSM into the task. Enable only when the overview stack has set initial_admin_password_hash — all three SSM params (/backend/initial_admin_password_hash, /backend/initial_admin_username, /backend/initial_admin_name) must exist before apply."
  type        = bool
  default     = false
}
