"""Operations API, run locally with Uvicorn or through API Gateway/Lambda."""

import json
import math
import os
import re
import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from botocore.exceptions import ClientError
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from mangum import Mangum
from pydantic import BaseModel, Field, field_validator

from pipeline import storage
from pipeline.common import aws, environment, event, now

app = FastAPI(title="Media Pipeline", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("ALLOWED_ORIGINS", "http://localhost:8000,http://localhost:5173").split(
        ","
    ),
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization"],
)


class UploadRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=160)
    contentType: Literal["video/mp4", "video/quicktime", "video/webm"]
    sizeBytes: int = Field(gt=0, le=100 * 1024 * 1024)

    @field_validator("filename")
    @classmethod
    def safe_filename(cls, value):
        if "/" in value or "\\" in value or value.startswith("."):
            raise ValueError("Filename must be a plain filename")
        if not value.lower().endswith((".mp4", ".mov", ".webm")):
            raise ValueError("Supported extensions: mp4, mov, webm")
        return re.sub(r"[^a-zA-Z0-9._ -]", "_", value)


class ScenarioRequest(BaseModel):
    scenario: Literal["duplicate", "corrupt", "retry", "burst", "dispatch-failure"]


def reserve_upload(request, scenario=None):
    job_id, timestamp = uuid.uuid4().hex, now()
    job = {
        "jobId": job_id,
        "filename": request.filename,
        "objectKey": f"uploads/{job_id}/{request.filename}",
        "status": "UPLOADING",
        "createdAt": timestamp,
        "updatedAt": timestamp,
        "sizeBytes": request.sizeBytes,
        "contentType": request.contentType,
        "outputs": {},
        "attemptCounts": {},
        "attempts": 0,
        "events": [event("upload_reserved", "Upload URL created; waiting for the source video")],
        "expiresAt": int(datetime.now(UTC).timestamp()) + 7 * 86400,
    }
    if scenario:
        job["scenario"] = scenario
    return storage.create_job(job)


def find_job(job_id):
    job = storage.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job


def public_job(job):
    result = {**job, "outputs": {}}
    for rendition, output in job.get("outputs", {}).items():
        result["outputs"][rendition] = {
            **output,
            "url": aws("s3", public=True).generate_presigned_url(
                "get_object",
                Params={"Bucket": os.environ["OUTPUT_BUCKET"], "Key": output["key"]},
                ExpiresIn=900,
            ),
        }
    return result


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "environment": environment(),
        "region": os.getenv("AWS_DEFAULT_REGION") or os.getenv("AWS_REGION", "eu-west-1"),
        "scenariosEnabled": os.getenv("ENABLE_SCENARIOS", "false").lower() == "true",
    }


@app.get("/api/jobs")
def jobs():
    return {"jobs": [public_job(job) for job in storage.list_jobs()]}


@app.get("/api/jobs/{job_id}")
def job_detail(job_id: str):
    return public_job(find_job(job_id))


@app.get("/api/jobs/{job_id}/history")
def job_history(job_id: str):
    job = find_job(job_id)
    result = {"events": sorted(job.get("events", []), key=lambda item: item["timestamp"])}
    if job.get("executionArn"):
        result["executionHistory"] = (
            aws("stepfunctions")
            .get_execution_history(
                executionArn=job["executionArn"],
                maxResults=100,
                reverseOrder=True,
                includeExecutionData=False,
            )
            .get("events", [])
        )
    return result


@app.post("/api/uploads", status_code=201)
def create_upload(request: UploadRequest):
    job = reserve_upload(request)
    return {
        "jobId": job["jobId"],
        "objectKey": job["objectKey"],
        "uploadUrl": aws("s3", public=True).generate_presigned_url(
            "put_object",
            Params={
                "Bucket": os.environ["INPUT_BUCKET"],
                "Key": job["objectKey"],
                "ContentType": request.contentType,
                "ContentLength": request.sizeBytes,
                "IfNoneMatch": "*",
            },
            ExpiresIn=900,
        ),
        "headers": {"Content-Type": request.contentType, "If-None-Match": "*"},
    }


@app.post("/api/jobs/{job_id}/confirm")
def confirm_upload(job_id: str):
    job = find_job(job_id)
    try:
        aws("s3").head_object(Bucket=os.environ["INPUT_BUCKET"], Key=job["objectKey"])
    except ClientError as exc:
        if exc.response["Error"]["Code"] in {"404", "NoSuchKey", "NotFound"}:
            raise HTTPException(409, "Upload has not arrived in S3") from exc
        raise
    if job["status"] == "UPLOADING":
        try:
            storage.update_job(
                job_id,
                {"status": "QUEUED"},
                "upload_received",
                "Source video stored in S3; awaiting dispatch",
                expected_status="UPLOADING",
            )
        except ClientError as exc:
            # A workflow may already have advanced this job; never downgrade it.
            if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
    return public_job(find_job(job_id))


def queue_depth(name):
    queue_url = os.getenv(name)
    if not queue_url:
        return 0
    response = aws("sqs").get_queue_attributes(
        QueueUrl=queue_url,
        AttributeNames=["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"],
    )["Attributes"]
    return sum(int(value) for value in response.values())


def percentile(values, quantile):
    if not values:
        return 0
    values = sorted(values)
    index = (len(values) - 1) * quantile
    lower, upper = math.floor(index), math.ceil(index)
    return round(values[lower] + (values[upper] - values[lower]) * (index - lower), 2)


@app.get("/api/metrics")
def metrics():
    records = storage.list_jobs()
    counts = Counter(job["status"] for job in records)
    durations = [
        job["durationSeconds"]
        for job in records
        if job["status"] == "SUCCEEDED" and "durationSeconds" in job
    ]
    current = datetime.now(UTC)
    series = []
    for offset in range(11, -1, -1):
        start = current.replace(second=0, microsecond=0) - timedelta(minutes=offset)
        end = start + timedelta(minutes=1)
        completed = [
            job
            for job in records
            if job.get("completedAt") and start <= datetime.fromisoformat(job["completedAt"]) < end
        ]
        successful = [job for job in completed if job["status"] == "SUCCEEDED"]
        series.append(
            {
                "timestamp": start.isoformat(),
                "completed": len(successful),
                "failed": len(completed) - len(successful),
                "avgDurationSeconds": round(
                    sum(job.get("durationSeconds", 0) for job in successful)
                    / max(1, len(successful)),
                    2,
                ),
            }
        )
    terminal = counts["SUCCEEDED"] + counts["FAILED"]
    return {
        "totalJobs": len(records),
        "succeeded": counts["SUCCEEDED"],
        "failed": counts["FAILED"],
        "active": sum(counts[status] for status in ("UPLOADING", "QUEUED", "RUNNING")),
        "successRate": round(100 * counts["SUCCEEDED"] / terminal, 1) if terminal else 0,
        "throughputPerMinute": sum(
            job["status"] == "SUCCEEDED"
            and job.get("completedAt", "") >= (current - timedelta(minutes=1)).isoformat()
            for job in records
        ),
        "p50Seconds": percentile(durations, 0.5),
        "p95Seconds": percentile(durations, 0.95),
        "queueDepth": queue_depth("INGEST_QUEUE_URL"),
        "deadLetterDepth": queue_depth("DLQ_URL"),
        "processingFailureDepth": queue_depth("FAILURE_QUEUE_URL"),
        "retryCount": sum(
            sum(max(0, count - 1) for count in job.get("attemptCounts", {}).values())
            for job in records
        ),
        "series": series,
        "renditions": [
            {"name": name, "count": sum(name in job.get("outputs", {}) for job in records)}
            for name in ("360p", "720p", "thumbnail")
        ],
        "sampleSize": len(durations),
        "historyLimit": 1000,
    }


def require_scenarios():
    if os.getenv("ENABLE_SCENARIOS", "false").lower() != "true":
        raise HTTPException(403, "Reliability scenarios are disabled in this environment")


def notification(job):
    return {
        "Records": [
            {
                "eventName": "ObjectCreated:Put",
                "s3": {
                    "bucket": {"name": os.environ["INPUT_BUCKET"]},
                    "object": {"key": job["objectKey"]},
                },
            }
        ]
    }


@app.post("/api/scenarios", status_code=202)
def scenario(request: ScenarioRequest):
    require_scenarios()
    if queue_depth("INGEST_QUEUE_URL") > 30:
        raise HTTPException(429, "Let the existing queue drain before adding another scenario")
    if request.scenario == "dispatch-failure":
        aws("sqs").send_message(
            QueueUrl=os.environ["INGEST_QUEUE_URL"],
            MessageBody=json.dumps({"invalid": "Deliberately malformed notification"}),
        )
        return {
            "message": "Malformed intake message submitted; it will reach the dispatch DLQ after retries",
            "jobIds": [],
        }
    source = Path(__file__).with_name("assets").joinpath("sample.mp4").read_bytes()
    jobs_created = []
    for index in range(5 if request.scenario == "burst" else 1):
        data = b"deliberately invalid media" if request.scenario == "corrupt" else source
        job = reserve_upload(
            UploadRequest(
                filename=f"{request.scenario}-{index + 1}.mp4",
                contentType="video/mp4",
                sizeBytes=len(data),
            ),
            request.scenario,
        )
        aws("s3").put_object(
            Bucket=os.environ["INPUT_BUCKET"],
            Key=job["objectKey"],
            Body=data,
            ContentType="video/mp4",
        )
        if request.scenario == "duplicate":
            # S3 supplies the first notification. Send two explicit duplicates.
            for _ in range(2):
                aws("sqs").send_message(
                    QueueUrl=os.environ["INGEST_QUEUE_URL"],
                    MessageBody=json.dumps(notification(job)),
                )
        jobs_created.append(job["jobId"])
    return {
        "message": f"{request.scenario.replace('-', ' ').title()} scenario submitted",
        "jobIds": jobs_created,
    }


@app.post("/api/dlq/redrive")
def redrive():
    require_scenarios()
    response = aws("sqs").receive_message(QueueUrl=os.environ["DLQ_URL"], MaxNumberOfMessages=10)
    count = 0
    for message in response.get("Messages", []):
        try:
            payload = json.loads(message["Body"])
            if not isinstance(payload, dict) or not payload.get("Records"):
                continue  # Poison messages need correction, not an infinite replay loop.
        except (ValueError, TypeError):
            continue
        aws("sqs").send_message(
            QueueUrl=os.environ["INGEST_QUEUE_URL"], MessageBody=message["Body"]
        )
        aws("sqs").delete_message(
            QueueUrl=os.environ["DLQ_URL"], ReceiptHandle=message["ReceiptHandle"]
        )
        count += 1
    return {
        "redriven": count,
        "message": "Valid notifications replayed; malformed messages remain for inspection",
    }


# Packaged frontend is optional for Lambda API-only deployment.
static_root = Path(os.getenv("WEB_DIST", str(Path(__file__).parents[2] / "web" / "dist")))
if static_root.is_dir():
    app.mount("/", StaticFiles(directory=static_root, html=True), name="dashboard")

handler = Mangum(app, lifespan="off")
