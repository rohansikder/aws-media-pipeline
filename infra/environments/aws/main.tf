terraform {
  required_version = ">= 1.10.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

# Credentials come from the ordinary AWS provider chain: a selected CLI/SSO
# profile locally or an OIDC role in CI. Do not put access keys in Terraform.
provider "aws" {
  region = var.region
}

variable "region" {
  type    = string
  default = "eu-west-1"
}

variable "availability_zone" {
  type    = string
  default = "eu-west-1a"
}

variable "name_prefix" {
  description = "Choose a globally unique lowercase prefix for S3 bucket names."
  type        = string
}

variable "worker_image" {
  description = "ECR image URI built from this repository; use an immutable tag or digest."
  type        = string
}

variable "allowed_origins" {
  type    = list(string)
  default = ["http://localhost:8000", "http://127.0.0.1:8000"]
}

module "pipeline" {
  source            = "../../modules/pipeline"
  name_prefix       = var.name_prefix
  region            = var.region
  availability_zone = var.availability_zone
  lambda_zip_path   = "${path.module}/../../../build/lambda.zip"
  worker_image      = var.worker_image
  allowed_origins   = var.allowed_origins
}

output "config" {
  value = module.pipeline.config
}

output "api_invoke_arn" {
  value = module.pipeline.api_invoke_arn
}

output "input_bucket" {
  value = module.pipeline.config.input_bucket
}

output "output_bucket" {
  value = module.pipeline.config.output_bucket
}

output "jobs_table" {
  value = module.pipeline.config.jobs_table
}

output "ingest_queue_url" {
  value = module.pipeline.config.ingest_queue_url
}

output "dlq_url" {
  value = module.pipeline.config.dlq_url
}

output "failure_queue_url" {
  value = module.pipeline.config.failure_queue_url
}

output "state_machine_arn" {
  value = module.pipeline.config.state_machine_arn
}
