variable "name_prefix" {
  description = "Unique deployment prefix; AWS S3 bucket names must be globally unique."
  type        = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,35}$", var.name_prefix))
    error_message = "Use 3–36 lowercase letters, digits or hyphens, starting with a letter."
  }
}

variable "region" {
  type    = string
  default = "eu-west-1"
}

variable "is_local" {
  type    = bool
  default = false
}

variable "lambda_zip_path" {
  description = "Existing deployment ZIP containing pipeline/ and its runtime dependencies."
  type        = string
}

variable "worker_image" {
  description = "Docker image URI: local image for Floci, pushed ECR image (preferably a digest) on AWS."
  type        = string
}

variable "allowed_origins" {
  type    = list(string)
  default = ["http://localhost:8000", "http://127.0.0.1:8000"]
}

variable "force_destroy_buckets" {
  description = "Permit Terraform destroy to remove nonempty buckets. Keep false for real deployments."
  type        = bool
  default     = false
}

variable "availability_zone" {
  type    = string
  default = "eu-west-1a"
}

variable "max_parallel_renditions" {
  description = "Per-workflow concurrency ceiling; not a global account-wide limit."
  type        = number
  default     = 2
  validation {
    condition     = var.max_parallel_renditions >= 1 && var.max_parallel_renditions <= 3
    error_message = "Choose an integer from 1 to 3."
  }
}

variable "log_retention_days" {
  type    = number
  default = 7
}

variable "tags" {
  type    = map(string)
  default = {}
}
