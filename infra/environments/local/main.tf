terraform {
  required_version = ">= 1.10.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region                      = "eu-west-1"
  access_key                  = "000000000000"
  secret_key                  = "test"
  skip_credentials_validation = true
  skip_metadata_api_check     = true
  skip_requesting_account_id  = true
  s3_use_path_style           = true
  endpoints {
    apigateway     = "http://localhost:4566"
    apigatewayv2   = "http://localhost:4566"
    cloudwatch     = "http://localhost:4566"
    cloudwatchlogs = "http://localhost:4566"
    dynamodb       = "http://localhost:4566"
    ec2            = "http://localhost:4566"
    ecr            = "http://localhost:4566"
    ecs            = "http://localhost:4566"
    iam            = "http://localhost:4566"
    lambda         = "http://localhost:4566"
    s3             = "http://localhost:4566"
    sqs            = "http://localhost:4566"
    sfn            = "http://localhost:4566"
    sts            = "http://localhost:4566"
  }
}

module "pipeline" {
  source                = "../../modules/pipeline"
  name_prefix           = "media-local"
  region                = "eu-west-1"
  is_local              = true
  lambda_zip_path       = "${path.module}/../../../build/lambda.zip"
  worker_image          = "media-pipeline-worker:local"
  force_destroy_buckets = true
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
