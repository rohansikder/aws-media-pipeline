resource "aws_cloudwatch_metric_alarm" "job_failures" {
  alarm_name          = "${var.name_prefix}-job-failures"
  alarm_description   = "At least one terminal workflow failure in five minutes"
  namespace           = "MediaPipeline"
  metric_name         = "JobsFailed"
  statistic           = "Sum"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  period              = 300
  evaluation_periods  = 1
  treat_missing_data  = "notBreaching"
  tags                = local.tags
}

resource "aws_cloudwatch_metric_alarm" "ingest_dlq" {
  alarm_name          = "${var.name_prefix}-ingest-dlq"
  alarm_description   = "Undispatched S3 notifications reached the dead-letter queue"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  dimensions          = { QueueName = aws_sqs_queue.ingest_dlq.name }
  statistic           = "Maximum"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  period              = 60
  evaluation_periods  = 1
  treat_missing_data  = "notBreaching"
  tags                = local.tags
}

resource "aws_cloudwatch_dashboard" "pipeline" {
  dashboard_name = "${var.name_prefix}-pipeline"
  dashboard_body = jsonencode({
    widgets = [
      {
        type = "metric", x = 0, y = 0, width = 12, height = 6
        properties = {
          title = "Workflow outcomes", region = var.region, stat = "Sum", period = 60
          metrics = [
            ["MediaPipeline", "JobsDispatched"],
            ["MediaPipeline", "JobsSucceeded"],
            ["MediaPipeline", "JobsFailed"]
          ]
        }
      },
      {
        type = "metric", x = 12, y = 0, width = 12, height = 6
        properties = {
          title = "Ingestion backlog", region = var.region, stat = "Maximum", period = 60
          metrics = [
            ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", aws_sqs_queue.ingest.name],
            ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", aws_sqs_queue.ingest_dlq.name]
          ]
        }
      }
    ]
  })
}
