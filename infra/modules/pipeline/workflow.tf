locals {
  control_retry = [{
    ErrorEquals     = ["States.TaskFailed"]
    IntervalSeconds = 2
    MaxAttempts     = 2
    BackoffRate     = 2
  }]
  worker_environment = concat([
    { Name = "JOB_ID", "Value.$" = "$.job.jobId" },
    { Name = "OBJECT_KEY", "Value.$" = "$.job.objectKey" },
    { Name = "SOURCE_BUCKET", "Value.$" = "$.job.bucket" },
    { Name = "OUTPUT_BUCKET", Value = aws_s3_bucket.media["output"].id },
    { Name = "JOBS_TABLE", Value = aws_dynamodb_table.jobs.name },
    { Name = "RENDITION", "Value.$" = "$.rendition" }
    ], var.is_local ? [
    { Name = "AWS_ENDPOINT_URL", Value = "http://floci:4566" },
    { Name = "AWS_ACCESS_KEY_ID", Value = "000000000000" },
    { Name = "AWS_SECRET_ACCESS_KEY", Value = "test" },
    { Name = "AWS_DEFAULT_REGION", Value = var.region }
  ] : [])
}

resource "aws_sfn_state_machine" "pipeline" {
  name     = "${var.name_prefix}-pipeline"
  role_arn = aws_iam_role.workflow.arn
  type     = "STANDARD"
  definition = jsonencode({
    Comment        = "Process media with bounded parallel container tasks and explicit terminal state"
    StartAt        = "MarkRunning"
    TimeoutSeconds = 1800
    States = {
      MarkRunning = {
        Type       = "Task"
        Resource   = aws_lambda_function.control.arn
        Parameters = { action = "start", "job.$" = "$" }
        Retry      = local.control_retry
        Catch      = [{ ErrorEquals = ["States.ALL"], ResultPath = "$.error", Next = "MarkFailed" }]
        Next       = "PrepareRenditions"
      }
      PrepareRenditions = {
        Type       = "Pass"
        Result     = ["360p", "720p", "thumbnail"]
        ResultPath = "$.renditions"
        Next       = "ProcessRenditions"
      }
      ProcessRenditions = {
        Type           = "Map"
        ItemsPath      = "$.renditions"
        MaxConcurrency = var.max_parallel_renditions
        ItemSelector   = { "rendition.$" = "$$.Map.Item.Value", "job.$" = "$" }
        ItemProcessor = {
          ProcessorConfig = { Mode = "INLINE" }
          StartAt         = "Transcode"
          States = {
            Transcode = {
              Type           = "Task"
              Resource       = "arn:${local.partition}:states:::ecs:runTask.sync"
              TimeoutSeconds = 180
              Parameters = {
                LaunchType     = "FARGATE"
                Cluster        = aws_ecs_cluster.pipeline.arn
                TaskDefinition = aws_ecs_task_definition.worker.arn
                NetworkConfiguration = {
                  AwsvpcConfiguration = {
                    Subnets        = [aws_subnet.workers.id]
                    SecurityGroups = [aws_security_group.workers.id]
                    AssignPublicIp = "DISABLED"
                  }
                }
                Overrides = {
                  ContainerOverrides = [{ Name = "worker", Environment = local.worker_environment }]
                }
              }
              Retry = [{
                ErrorEquals     = ["States.TaskFailed", "States.Timeout"]
                IntervalSeconds = 2
                MaxAttempts     = 2
                BackoffRate     = 2
                JitterStrategy  = "FULL"
              }]
              ResultPath = null
              End        = true
            }
          }
        }
        ResultPath = null
        Catch      = [{ ErrorEquals = ["States.ALL"], ResultPath = "$.error", Next = "MarkFailed" }]
        Next       = "MarkComplete"
      }
      MarkComplete = {
        Type       = "Task"
        Resource   = aws_lambda_function.control.arn
        Parameters = { action = "complete", "job.$" = "$" }
        Retry      = local.control_retry
        Catch      = [{ ErrorEquals = ["States.ALL"], ResultPath = "$.error", Next = "MarkFailed" }]
        End        = true
      }
      MarkFailed = {
        Type       = "Task"
        Resource   = aws_lambda_function.control.arn
        Parameters = { action = "fail", "job.$" = "$", "error.$" = "$.error" }
        Retry      = local.control_retry
        ResultPath = null
        Next       = "PipelineFailed"
      }
      PipelineFailed = {
        Type  = "Fail"
        Error = "MediaProcessingFailed"
        Cause = "Processing exhausted retries; see job state and execution history."
      }
    }
  })
  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.workflow.arn}:*"
    include_execution_data = false
    level                  = "ALL"
  }
  tags = local.tags
  depends_on = [
    aws_iam_role_policy.workflow,
    aws_vpc_endpoint.gateway,
    aws_vpc_endpoint.interface
  ]
}
