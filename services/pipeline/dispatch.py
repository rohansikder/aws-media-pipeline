"""SQS ingestion Lambda: acknowledge only successful or duplicate dispatches."""

import json
import os
import re
from urllib.parse import unquote_plus

from botocore.exceptions import ClientError

from pipeline import storage
from pipeline.common import aws, log, metric

KEY_PATTERN = re.compile(r"^uploads/([a-f0-9]{32})/[^/]+$")


def dispatch_s3(record):
    if not record.get("eventName", "").startswith("ObjectCreated:"):
        return
    bucket = record["s3"]["bucket"]["name"]
    key = unquote_plus(record["s3"]["object"]["key"])
    match = KEY_PATTERN.fullmatch(key)
    if bucket != os.environ["INPUT_BUCKET"] or not match:
        raise ValueError("Unexpected upload bucket or object key")
    job_id = match.group(1)
    job = storage.get_job(job_id)
    if not job or job["objectKey"] != key:
        raise ValueError("Upload has no matching job reservation")
    payload = {"jobId": job_id, "objectKey": key, "bucket": bucket}
    try:
        result = aws("stepfunctions").start_execution(
            stateMachineArn=os.environ["STATE_MACHINE_ARN"],
            name=f"job-{job_id}",
            input=json.dumps(payload, sort_keys=True, separators=(",", ":")),
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ExecutionAlreadyExists":
            raise
        log("duplicate_dispatch", jobId=job_id)
        metric("DuplicateEvents")
        return
    # Preserve RUNNING/SUCCEEDED if the workflow raced this metadata update.
    storage.update_job(job_id, {"executionArn": result["executionArn"]})
    metric("JobsDispatched")
    log("job_dispatched", jobId=job_id, executionArn=result["executionArn"])


def handler(event, context):
    failures = []
    for message in event.get("Records", []):
        try:
            payload = json.loads(message["body"])
            if payload.get("Event") == "s3:TestEvent":
                continue
            records = payload.get("Records")
            if not isinstance(records, list) or not records:
                raise ValueError("Expected an S3 object notification")
            for record in records:
                dispatch_s3(record)
        except Exception as exc:
            log("dispatch_failed", messageId=message["messageId"], error=str(exc))
            failures.append({"itemIdentifier": message["messageId"]})
    return {"batchItemFailures": failures}
