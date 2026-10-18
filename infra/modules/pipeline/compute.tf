locals {
  lambda_environment = merge({
    INPUT_BUCKET            = aws_s3_bucket.media["input"].id
    OUTPUT_BUCKET           = aws_s3_bucket.media["output"].id
    JOBS_TABLE              = aws_dynamodb_table.jobs.name
    INGEST_QUEUE_URL        = aws_sqs_queue.ingest.id
    INGEST_DLQ_URL          = aws_sqs_queue.ingest_dlq.id
    DLQ_URL                 = aws_sqs_queue.ingest_dlq.id
    FAILURE_QUEUE_URL       = aws_sqs_queue.failed_jobs.id
    ALLOWED_ORIGINS         = join(",", var.allowed_origins)
    METRICS_NAMESPACE       = "MediaPipeline"
    ENABLE_SCENARIOS        = var.is_local ? "true" : "false"
    AWS_S3_ADDRESSING_STYLE = var.is_local ? "path" : "virtual"
    POWERTOOLS_SERVICE_NAME = "media-pipeline"
    }, var.is_local ? {
    AWS_ENDPOINT_URL        = "http://floci:4566"
    PUBLIC_AWS_ENDPOINT_URL = "http://localhost:4566"
  } : {})
}

resource "aws_cloudwatch_log_group" "lambda" {
  for_each          = local.lambda_names
  name              = "/aws/lambda/${each.value}"
  retention_in_days = var.log_retention_days
  tags              = local.tags
}

resource "aws_cloudwatch_log_group" "worker" {
  name              = "/ecs/${var.name_prefix}-worker"
  retention_in_days = var.log_retention_days
  tags              = local.tags
}

resource "aws_cloudwatch_log_group" "workflow" {
  name              = "/aws/vendedlogs/states/${var.name_prefix}-pipeline"
  retention_in_days = var.log_retention_days
  tags              = local.tags
}

resource "aws_lambda_function" "control" {
  function_name    = local.lambda_names["control"]
  role             = aws_iam_role.lambda["control"].arn
  filename         = var.lambda_zip_path
  source_code_hash = filebase64sha256(var.lambda_zip_path)
  runtime          = "python3.12"
  handler          = "pipeline.control.handler"
  architectures    = ["x86_64"]
  timeout          = 30
  memory_size      = 256
  environment {
    variables = local.lambda_environment
  }
  tags       = local.tags
  depends_on = [aws_iam_role_policy.control, aws_iam_role_policy.lambda_logs]
}

resource "aws_lambda_function" "dispatch" {
  function_name    = local.lambda_names["dispatch"]
  role             = aws_iam_role.lambda["dispatch"].arn
  filename         = var.lambda_zip_path
  source_code_hash = filebase64sha256(var.lambda_zip_path)
  runtime          = "python3.12"
  handler          = "pipeline.dispatch.handler"
  architectures    = ["x86_64"]
  timeout          = var.is_local ? 10 : 30
  memory_size      = 256
  environment {
    variables = merge(local.lambda_environment, { STATE_MACHINE_ARN = aws_sfn_state_machine.pipeline.arn })
  }
  tags       = local.tags
  depends_on = [aws_iam_role_policy.dispatch, aws_iam_role_policy.lambda_logs]
}

resource "aws_lambda_function" "api" {
  function_name    = local.lambda_names["api"]
  role             = aws_iam_role.lambda["api"].arn
  filename         = var.lambda_zip_path
  source_code_hash = filebase64sha256(var.lambda_zip_path)
  runtime          = "python3.12"
  handler          = "pipeline.api.handler"
  architectures    = ["x86_64"]
  timeout          = 30
  memory_size      = 512
  environment {
    variables = merge(local.lambda_environment, { STATE_MACHINE_ARN = aws_sfn_state_machine.pipeline.arn })
  }
  tags       = local.tags
  depends_on = [aws_iam_role_policy.api, aws_iam_role_policy.lambda_logs]
}

resource "aws_lambda_event_source_mapping" "ingest" {
  event_source_arn        = aws_sqs_queue.ingest.arn
  function_name           = aws_lambda_function.dispatch.arn
  batch_size              = 5
  function_response_types = ["ReportBatchItemFailures"]
  # This limit is enforced on AWS; Floci 2.3.0 stores it but dispatches one batch
  # at a time for each event-source mapping.
  scaling_config {
    maximum_concurrency = 2
  }
  depends_on = [aws_iam_role_policy.dispatch]
}

resource "aws_ecr_repository" "worker" {
  count                = var.is_local ? 0 : 1
  name                 = "${var.name_prefix}-worker"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = false
  image_scanning_configuration {
    scan_on_push = true
  }
  tags = local.tags
}

resource "aws_ecs_cluster" "pipeline" {
  name = "${var.name_prefix}-workers"
  setting {
    name  = "containerInsights"
    value = var.is_local ? "disabled" : "enabled"
  }
  tags = local.tags
}

resource "aws_ecs_task_definition" "worker" {
  family                   = "${var.name_prefix}-worker"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.worker.arn
  runtime_platform {
    cpu_architecture        = "X86_64"
    operating_system_family = "LINUX"
  }
  container_definitions = jsonencode([{
    name      = "worker"
    image     = var.worker_image
    essential = true
    environment = [
      { name = "OUTPUT_BUCKET", value = aws_s3_bucket.media["output"].id },
      { name = "JOBS_TABLE", value = aws_dynamodb_table.jobs.name },
      { name = "METRICS_NAMESPACE", value = "MediaPipeline" },
      { name = "AWS_DEFAULT_REGION", value = var.region }
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.worker.name
        awslogs-region        = var.region
        awslogs-stream-prefix = "rendition"
      }
    }
  }])
  tags = local.tags
}

# Real AWS endpoints require SigV4 IAM authorization. The local dashboard talks
# to the FastAPI process directly; local API Gateway remains useful for parity.
resource "aws_apigatewayv2_api" "pipeline" {
  name          = "${var.name_prefix}-api"
  protocol_type = "HTTP"
  cors_configuration {
    allow_origins = var.allowed_origins
    allow_methods = ["GET", "POST", "OPTIONS"]
    allow_headers = ["content-type", "authorization", "x-amz-date", "x-amz-security-token", "x-amz-content-sha256"]
    max_age       = 300
  }
  tags = local.tags
}

resource "aws_apigatewayv2_integration" "api" {
  api_id                 = aws_apigatewayv2_api.pipeline.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.api.invoke_arn
  payload_format_version = "2.0"
  timeout_milliseconds   = 30000
}

resource "aws_apigatewayv2_route" "api" {
  api_id             = aws_apigatewayv2_api.pipeline.id
  route_key          = "$default"
  target             = "integrations/${aws_apigatewayv2_integration.api.id}"
  authorization_type = var.is_local ? "NONE" : "AWS_IAM"
}

resource "aws_apigatewayv2_stage" "api" {
  api_id      = aws_apigatewayv2_api.pipeline.id
  name        = "$default"
  auto_deploy = true
  default_route_settings {
    throttling_burst_limit = 20
    throttling_rate_limit  = 10
  }
  tags = local.tags
}

resource "aws_lambda_permission" "api_gateway" {
  statement_id  = "AllowApiGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.api.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.pipeline.execution_arn}/*/*"
}
