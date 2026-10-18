"""Workflow lifecycle Lambda, separate from per-rendition worker updates."""

import json
import os
from datetime import datetime

from pipeline import storage
from pipeline.common import aws, log, metric, now


def handler(event, context):
    action, payload = event["action"], event["job"]
    job_id = payload["jobId"]
    job = storage.get_job(job_id)
    if not job:
        raise ValueError("Job reservation does not exist")
    if action == "start":
        storage.update_job(
            job_id,
            {"status": "RUNNING", "startedAt": now()},
            "workflow_started",
            "Workflow started; processing three renditions",
        )
    elif action in {"complete", "fail"}:
        duration = max(
            0.0,
            (
                datetime.fromisoformat(now())
                - datetime.fromisoformat(job.get("startedAt", job["createdAt"]))
            ).total_seconds(),
        )
        fields = {
            "status": "SUCCEEDED" if action == "complete" else "FAILED",
            "completedAt": now(),
            "durationSeconds": round(duration, 3),
        }
        if action == "complete":
            if set(job.get("outputs", {})) != {"360p", "720p", "thumbnail"}:
                raise ValueError("Cannot complete a job without all three output renditions")
            message = "All renditions published successfully"
        else:
            error = event.get("error", {})
            fields["error"] = (
                json.dumps(error)[:3000] if not isinstance(error, str) else error[:3000]
            )
            message = "Processing failed; see rendition events for diagnostics"
        storage.update_job(job_id, fields, f"workflow_{fields['status'].lower()}", message)
        metric("JobsSucceeded" if action == "complete" else "JobsFailed")
        metric("JobDuration", duration, "Seconds")
        if action == "fail":
            aws("sqs").send_message(
                QueueUrl=os.environ["FAILURE_QUEUE_URL"],
                MessageBody=json.dumps({"jobId": job_id, "error": fields["error"]}),
            )
    else:
        raise ValueError(f"Unknown workflow action: {action}")
    log("workflow_transition", jobId=job_id, action=action)
    return payload
