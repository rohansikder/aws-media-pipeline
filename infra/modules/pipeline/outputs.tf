output "config" {
  description = "Application and bootstrap configuration; no credentials."
  value = {
    region                 = var.region
    input_bucket           = aws_s3_bucket.media["input"].id
    output_bucket          = aws_s3_bucket.media["output"].id
    jobs_table             = aws_dynamodb_table.jobs.name
    ingest_queue_url       = aws_sqs_queue.ingest.id
    ingest_queue_arn       = aws_sqs_queue.ingest.arn
    ingest_dlq_url         = aws_sqs_queue.ingest_dlq.id
    dlq_url                = aws_sqs_queue.ingest_dlq.id
    failure_queue_url      = aws_sqs_queue.failed_jobs.id
    state_machine_arn      = aws_sfn_state_machine.pipeline.arn
    dispatcher_function    = aws_lambda_function.dispatch.function_name
    api_function           = aws_lambda_function.api.function_name
    control_function       = aws_lambda_function.control.function_name
    api_gateway_endpoint   = aws_apigatewayv2_api.pipeline.api_endpoint
    api_endpoint           = var.is_local ? "http://localhost:8000" : aws_apigatewayv2_api.pipeline.api_endpoint
    ecs_cluster            = aws_ecs_cluster.pipeline.arn
    worker_task_definition = aws_ecs_task_definition.worker.arn
    worker_image           = var.worker_image
    worker_repository_url  = var.is_local ? "" : aws_ecr_repository.worker[0].repository_url
    metrics_namespace      = "MediaPipeline"
    allowed_origins        = join(",", var.allowed_origins)
    enable_scenarios       = var.is_local
  }
}

output "api_invoke_arn" {
  description = "Scope an operator's execute-api:Invoke permission to this API ARN."
  value       = "${aws_apigatewayv2_api.pipeline.execution_arn}/*/*"
}
