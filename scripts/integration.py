"""Exercise the real local event path; never substitute a direct handler call.

Requires an already provisioned Floci environment. ``make integration`` starts it.
Only local test resources are used; diagnostic queue messages are left intact.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi.testclient import TestClient
from local_env import load

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "build" / "integration-results.json"
CASES = ("upload", "duplicate", "retry", "corrupt", "burst", "dispatch-failure")
RENDITIONS = {"360p", "720p", "thumbnail"}


class DeadlineExceeded(TimeoutError):
    """The integration suite exhausted its bounded wall-clock budget."""


def utc_now():
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def alarm_handler(_signum, _frame):
    raise DeadlineExceeded("Integration suite reached its global deadline")


def revision():
    try:
        return subprocess.check_output(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=3,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return None


class Integration:
    def __init__(self, timeout_seconds):
        self.started = time.monotonic()
        self.deadline = self.started + timeout_seconds
        self.case_deadline = self.deadline
        self.api = None
        self.http = None
        self.current = None
        self.report = {
            "schemaVersion": 1,
            "startedAt": utc_now(),
            "status": "running",
            "gitRevision": revision(),
            "environment": {
                "name": "local-floci",
                "pythonVersion": platform.python_version(),
                "ffprobeAvailable": bool(shutil.which("ffprobe")),
                "globalDeadlineSeconds": timeout_seconds,
                "perCaseDeadlineSeconds": 180,
            },
            "methodology": {
                "path": "API reservation → real S3 PUT → S3 notification → SQS → Lambda → Step Functions → ECS/FFmpeg → S3/DynamoDB",
                "apiTransport": "FastAPI TestClient; AWS SDK and signed S3 HTTP requests use the running emulator",
                "timestampUnit": "UTC ISO 8601",
                "durationUnit": "seconds",
                "scope": "Functional local integration evidence; not AWS latency, throughput, availability, or IAM validation",
            },
            "cases": [],
        }

    def write(self):
        RESULTS.parent.mkdir(parents=True, exist_ok=True)
        RESULTS.write_text(json.dumps(self.report, indent=2, default=str) + "\n")

    def check_time(self):
        if time.monotonic() >= self.deadline:
            raise DeadlineExceeded("Integration suite reached its global deadline")
        if time.monotonic() >= self.case_deadline:
            raise TimeoutError("Case did not finish within 180 seconds")

    def wait(self, description, probe):
        while True:
            try:
                self.check_time()
            except DeadlineExceeded:
                raise
            except TimeoutError as error:
                raise TimeoutError(f"Timed out waiting for {description}") from error
            value = probe()
            if value:
                return value
            time.sleep(min(1, max(0, self.case_deadline - time.monotonic())))

    def request(self, method, path, expected=200, **kwargs):
        self.check_time()
        response = self.api.request(method, path, **kwargs)
        require(
            response.status_code == expected,
            f"{method} {path}: expected HTTP {expected}, got {response.status_code}: {response.text[:500]}",
        )
        return response.json()

    def setup(self):
        configuration = load()["config"]
        endpoint = urlsplit(os.environ["AWS_ENDPOINT_URL"])
        require(
            endpoint.scheme == "http" and endpoint.hostname in {"localhost", "127.0.0.1", "::1"},
            "Integration is restricted to the local emulator endpoint",
        )
        # Import after local_env.load(), so no client can bind to another AWS environment.
        sys.path.insert(0, str(ROOT / "services"))
        from pipeline import storage
        from pipeline.api import app
        from pipeline.common import aws

        aws.cache_clear()
        self.aws, self.storage = aws, storage
        self.api = TestClient(app)
        self.http = httpx.Client(timeout=30, follow_redirects=False, trust_env=False)
        self.report["environment"].update(
            endpoint=os.environ["AWS_ENDPOINT_URL"],
            region=os.environ["AWS_DEFAULT_REGION"],
            inputBucket=os.environ["INPUT_BUCKET"],
            outputBucket=os.environ["OUTPUT_BUCKET"],
            jobsTable=os.environ["JOBS_TABLE"],
            stateMachineArn=os.environ["STATE_MACHINE_ARN"],
        )
        health = self.request("GET", "/api/health")
        require(
            health["status"] == "ok" and health["scenariosEnabled"],
            "Local scenarios must be enabled",
        )
        gateway_host = urlsplit(configuration["api_gateway_endpoint"]).netloc
        require(bool(gateway_host), "Terraform must expose the API Gateway routing host")
        gateway_url = os.environ["AWS_ENDPOINT_URL"].rstrip("/") + "/api/health"
        # Route through Floci's local listener; never contact the AWS-shaped endpoint.
        gateway_response = self.http.get(gateway_url, headers={"Host": gateway_host})
        self.report["apiGatewaySmoke"] = {
            "localUrl": gateway_url,
            "hostHeader": gateway_host,
            "transport": "Local HTTP API Gateway → real API Lambda → FastAPI",
            "status": gateway_response.status_code,
        }
        require(
            gateway_response.status_code == 200,
            f"Local API Gateway health returned HTTP {gateway_response.status_code}",
        )
        gateway_health = gateway_response.json()
        self.report["apiGatewaySmoke"]["health"] = gateway_health
        require(
            isinstance(gateway_health, dict) and gateway_health.get("status") == "ok",
            "Local API Gateway must invoke a healthy API Lambda",
        )
        self.report["initialMetrics"] = self.request("GET", "/api/metrics")
        self.source = (ROOT / "services" / "pipeline" / "assets" / "sample.mp4").read_bytes()
        self.report["fixture"] = {
            "path": "services/pipeline/assets/sample.mp4",
            "sizeBytes": len(self.source),
            "sha256": hashlib.sha256(self.source).hexdigest(),
        }

    def queue_depth(self, env_name):
        attributes = self.aws("sqs").get_queue_attributes(
            QueueUrl=os.environ[env_name],
            AttributeNames=["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"],
        )["Attributes"]
        return sum(int(value) for value in attributes.values())

    def terminal_jobs(self, job_ids, expected_status):
        last = {}

        def probe():
            for job_id in job_ids:
                last[job_id] = self.storage.get_job(job_id)
                job = last[job_id]
                if job and job["status"] in {"SUCCEEDED", "FAILED"}:
                    require(
                        job["status"] == expected_status,
                        f"Job {job_id} finished {job['status']}, expected {expected_status}: {job.get('error', '')}",
                    )
            return (
                list(last.values())
                if all(
                    job and job["status"] == expected_status and job.get("executionArn")
                    for job in last.values()
                )
                else None
            )

        try:
            return self.wait(f"jobs to reach {expected_status}", probe)
        except Exception:
            self.current["lastObservedJobs"] = last
            raise

    def execution_count(self, job_id):
        matches, token = [], None
        while True:
            self.check_time()
            arguments = {"stateMachineArn": os.environ["STATE_MACHINE_ARN"], "maxResults": 1000}
            if token:
                arguments["nextToken"] = token
            response = self.aws("stepfunctions").list_executions(**arguments)
            matches.extend(
                item for item in response["executions"] if item["name"] == f"job-{job_id}"
            )
            token = response.get("nextToken")
            if not token:
                return len(matches)

    def history(self, job):
        expected = "SUCCEEDED" if job["status"] == "SUCCEEDED" else "FAILED"

        def probe():
            execution = self.aws("stepfunctions").describe_execution(
                executionArn=job["executionArn"]
            )
            if execution["status"] == "RUNNING":
                return None
            require(
                execution["status"] == expected,
                f"Unexpected workflow result: {execution['status']}",
            )
            return execution

        execution = self.wait("workflow terminal history", probe)
        history = self.request("GET", f"/api/jobs/{job['jobId']}/history")
        require(bool(history.get("events")), "API must expose persisted job events")
        require(
            bool(history.get("executionHistory")), "API must expose real Step Functions history"
        )
        return {
            "executionArn": job["executionArn"],
            "executionStatus": execution["status"],
            "executionCount": self.execution_count(job["jobId"]),
            "jobEventCount": len(history["events"]),
            "executionEventCount": len(history["executionHistory"]),
            "executionEventTypes": sorted({item["type"] for item in history["executionHistory"]}),
        }

    def probe_output(self, data, rendition):
        if not shutil.which("ffprobe"):
            return {"status": "unavailable"}
        suffix = ".jpg" if rendition == "thumbnail" else ".mp4"
        with tempfile.NamedTemporaryFile(suffix=suffix) as file:
            file.write(data)
            file.flush()
            output = subprocess.check_output(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-select_streams",
                    "v:0",
                    "-show_entries",
                    "stream=codec_name,width,height:format=duration",
                    "-of",
                    "json",
                    file.name,
                ],
                timeout=min(15, max(0.1, self.case_deadline - time.monotonic())),
                stderr=subprocess.STDOUT,
                text=True,
            )
        info = json.loads(output)
        require(bool(info.get("streams")), f"{rendition} has no decodable video/image stream")
        stream = info["streams"][0]
        expected_codec = "mjpeg" if rendition == "thumbnail" else "h264"
        require(stream["codec_name"] == expected_codec, f"{rendition} has the wrong codec")
        max_width, max_height = (1280, 720) if rendition == "720p" else (640, 360)
        require(
            0 < stream["width"] <= max_width and 0 < stream["height"] <= max_height,
            f"{rendition} exceeds its resolution bounds",
        )
        result = {"status": "passed", **stream}
        if rendition != "thumbnail":
            duration = float(info.get("format", {}).get("duration", 0))
            require(math.isfinite(duration) and 0 < duration <= 120.1, "Video duration is invalid")
            result["durationSeconds"] = duration
        return result

    def outputs(self, job):
        require(
            set(job.get("outputs", {})) == RENDITIONS, "Expected exactly three published renditions"
        )
        verified = {}
        for rendition, output in sorted(job["outputs"].items()):
            self.check_time()
            extension, content_type = (
                ("jpg", "image/jpeg") if rendition == "thumbnail" else ("mp4", "video/mp4")
            )
            attempt = int(job["attemptCounts"][rendition])
            require(
                output["key"] == f"jobs/{job['jobId']}/{rendition}/attempt-{attempt}.{extension}",
                "Unexpected output key",
            )
            response = self.aws("s3").get_object(
                Bucket=os.environ["OUTPUT_BUCKET"], Key=output["key"]
            )
            try:
                data = response["Body"].read()
            finally:
                response["Body"].close()
            require(
                len(data) > 16 and len(data) == int(output["sizeBytes"]), "Output size mismatch"
            )
            require(response["ContentType"] == content_type, "Incorrect S3 content type")
            require(
                response.get("Metadata", {}).get("job-id") == job["jobId"], "Missing job metadata"
            )
            require(
                response.get("Metadata", {}).get("rendition") == rendition,
                "Missing rendition metadata",
            )
            if rendition == "thumbnail":
                require(
                    data.startswith(b"\xff\xd8\xff") and data.endswith(b"\xff\xd9"),
                    "Invalid JPEG signature",
                )
            else:
                require(data[4:8] == b"ftyp", "Invalid MP4 container signature")
            verified[rendition] = {
                "key": output["key"],
                "sizeBytes": len(data),
                "contentType": content_type,
                "sha256": hashlib.sha256(data).hexdigest(),
                "signatureVerified": True,
                "ffprobe": self.probe_output(data, rendition),
            }
        keys = (
            self.aws("s3")
            .list_objects_v2(Bucket=os.environ["OUTPUT_BUCKET"], Prefix=f"jobs/{job['jobId']}/")
            .get("Contents", [])
        )
        require(len(keys) == 3, "Expected exactly three objects under the job output prefix")
        return verified

    def success(self, job, expected_attempts=None):
        history = self.history(job)
        require(history["executionCount"] == 1, "A job must have exactly one workflow execution")
        if expected_attempts is not None:
            require(
                job["attemptCounts"] == expected_attempts,
                f"Unexpected attempts: {job['attemptCounts']}",
            )
            require(
                job["attempts"] == sum(expected_attempts.values()), "Total attempt count mismatch"
            )
        return {
            "jobId": job["jobId"],
            "status": job["status"],
            "createdAt": job["createdAt"],
            "completedAt": job["completedAt"],
            "durationSeconds": job["durationSeconds"],
            "attempts": job["attempts"],
            "attemptCounts": job["attemptCounts"],
            "history": history,
            "outputs": self.outputs(job),
        }

    def submit(self, scenario):
        response = self.request("POST", "/api/scenarios", expected=202, json={"scenario": scenario})
        self.current["jobIds"] = response["jobIds"]
        return response["jobIds"]

    def upload(self):
        upload = self.request(
            "POST",
            "/api/uploads",
            expected=201,
            json={
                "filename": "integration-sample.mp4",
                "contentType": "video/mp4",
                "sizeBytes": len(self.source),
            },
        )
        self.current["jobIds"] = [upload["jobId"]]
        require(
            urlsplit(upload["uploadUrl"]).hostname in {"localhost", "127.0.0.1", "::1"},
            "Signed upload must target the local emulator",
        )
        require(upload["headers"].get("If-None-Match") == "*", "Upload must require a new object")
        response = self.http.put(
            upload["uploadUrl"], headers=upload["headers"], content=self.source
        )
        require(
            response.status_code in {200, 201, 204}, f"Signed S3 PUT failed: {response.status_code}"
        )
        # Keep the signed content length intact while attempting to change existing bytes.
        replacement = self.source[:-1] + bytes([self.source[-1] ^ 1])
        overwrite = self.http.put(
            upload["uploadUrl"], headers=upload["headers"], content=replacement
        )
        require(
            overwrite.status_code == 412, "Repeating a write-once signed PUT must return HTTP 412"
        )
        original = self.aws("s3").get_object(
            Bucket=os.environ["INPUT_BUCKET"], Key=upload["objectKey"]
        )
        try:
            require(
                original["Body"].read() == self.source,
                "Rejected overwrite changed the source bytes",
            )
        finally:
            original["Body"].close()
        self.request("POST", f"/api/jobs/{upload['jobId']}/confirm")
        job = self.terminal_jobs([upload["jobId"]], "SUCCEEDED")[0]
        result = self.success(job, dict.fromkeys(RENDITIONS, 1))
        public = self.request("GET", f"/api/jobs/{job['jobId']}")
        for output in public["outputs"].values():
            download = self.http.get(output["url"])
            require(download.status_code == 200, "Published signed download URL did not work")
            require(len(download.content) == output["sizeBytes"], "Signed download size mismatch")
        self.current.update(
            jobs=[result],
            signedUploadVerified=True,
            signedDownloadsVerified=3,
            overwriteRejectedStatus=overwrite.status_code,
            originalSourceUnchanged=True,
        )

    def duplicate(self):
        job = self.terminal_jobs(self.submit("duplicate"), "SUCCEEDED")[0]
        self.wait(
            "duplicate notifications to be acknowledged",
            lambda: self.queue_depth("INGEST_QUEUE_URL") == 0,
        )
        # Re-read after the duplicates have drained; detecting only the first completion is insufficient.
        job = self.storage.get_job(job["jobId"])
        require(
            sum(item["type"] == "workflow_started" for item in job["events"]) == 1,
            "Duplicate notifications started more than one workflow",
        )
        self.current.update(
            submittedNotifications=3,
            jobs=[self.success(job, dict.fromkeys(RENDITIONS, 1))],
            ingestDrained=True,
        )

    def retry(self):
        job = self.terminal_jobs(self.submit("retry"), "SUCCEEDED")[0]
        failed = [
            item
            for item in job["events"]
            if item["type"] == "worker_failed" and item.get("rendition") == "360p"
        ]
        require(
            any(item.get("attempt") == 1 for item in failed), "First 360p failure was not recorded"
        )
        self.current["jobs"] = [self.success(job, {"360p": 2, "720p": 1, "thumbnail": 1})]
        self.current["controlledFailureVerified"] = True

    def queue_message(self, env_name, predicate):
        response = self.aws("sqs").receive_message(
            QueueUrl=os.environ[env_name],
            MaxNumberOfMessages=10,
            WaitTimeSeconds=0,
            VisibilityTimeout=0,
            MessageSystemAttributeNames=["ApproximateReceiveCount"],
        )
        for message in response.get("Messages", []):
            try:
                body = json.loads(message["Body"])
            except (TypeError, ValueError):
                continue
            if predicate(body):
                return {"messageId": message["MessageId"], "body": body}
        return None

    def corrupt(self):
        job = self.terminal_jobs(self.submit("corrupt"), "FAILED")[0]
        require(not job.get("outputs"), "Corrupt input unexpectedly published a rendition")
        keys = (
            self.aws("s3")
            .list_objects_v2(Bucket=os.environ["OUTPUT_BUCKET"], Prefix=f"jobs/{job['jobId']}/")
            .get("Contents", [])
        )
        require(not keys, "Corrupt input left published S3 objects")
        notification = self.wait(
            "processing failure notification",
            lambda: self.queue_message(
                "FAILURE_QUEUE_URL",
                lambda body: isinstance(body, dict) and body.get("jobId") == job["jobId"],
            ),
        )
        history = self.history(job)
        require(history["executionCount"] == 1, "Expected one failed execution")
        self.current.update(
            jobs=[
                {
                    "jobId": job["jobId"],
                    "status": job["status"],
                    "attempts": job["attempts"],
                    "attemptCounts": job["attemptCounts"],
                    "outputs": {},
                    "history": history,
                    "durationSeconds": job["durationSeconds"],
                    "error": job.get("error"),
                }
            ],
            failureNotification=notification,
            publishedObjectCount=0,
        )

    def burst(self):
        job_ids = self.submit("burst")
        require(
            len(job_ids) == 5 and len(set(job_ids)) == 5, "Burst must create five distinct jobs"
        )
        jobs = self.terminal_jobs(job_ids, "SUCCEEDED")
        self.current["jobs"] = [self.success(job, dict.fromkeys(RENDITIONS, 1)) for job in jobs]

    def dispatch_failure(self):
        baseline = self.queue_depth("DLQ_URL")
        attributes = self.aws("sqs").get_queue_attributes(
            QueueUrl=os.environ["INGEST_QUEUE_URL"],
            AttributeNames=["VisibilityTimeout", "RedrivePolicy"],
        )["Attributes"]
        self.current["queuePolicy"] = {
            "visibilityTimeoutSeconds": int(attributes["VisibilityTimeout"]),
            "maxReceiveCount": int(json.loads(attributes["RedrivePolicy"])["maxReceiveCount"]),
        }
        require(
            self.submit("dispatch-failure") == [], "Malformed dispatch must not reserve a media job"
        )
        self.wait("intake DLQ depth to increase", lambda: self.queue_depth("DLQ_URL") > baseline)
        notification = self.wait(
            "malformed intake message in DLQ",
            lambda: self.queue_message(
                "DLQ_URL",
                lambda body: isinstance(body, dict)
                and body.get("invalid") == "Deliberately malformed notification",
            ),
        )
        self.current.update(
            deadLetterDepthBefore=baseline,
            deadLetterDepthAfter=self.queue_depth("DLQ_URL"),
            deadLetterMessage=notification,
            diagnosticMessageRetained=True,
        )

    def run_case(self, name):
        started = time.monotonic()
        self.case_deadline = min(self.deadline, started + 180)
        self.current = {"name": name, "status": "running", "startedAt": utc_now()}
        self.report["cases"].append(self.current)
        self.write()
        print(f"[integration] {name}: started", flush=True)
        try:
            getattr(self, name.replace("-", "_"))()
            self.current["status"] = "passed"
        except DeadlineExceeded:
            self.current.update(status="failed", error="Global integration deadline exceeded")
            raise
        except Exception as error:
            self.current.update(status="failed", error=f"{type(error).__name__}: {error}")
            traceback.print_exc()
        finally:
            self.current.update(
                finishedAt=utc_now(), elapsedSeconds=round(time.monotonic() - started, 3)
            )
            self.write()
            print(
                f"[integration] {name}: {self.current['status']} ({self.current['elapsedSeconds']}s)",
                flush=True,
            )

    def finish(self):
        self.report.update(
            finishedAt=utc_now(), elapsedSeconds=round(time.monotonic() - self.started, 3)
        )
        cases = self.report["cases"]
        passed = sum(case["status"] == "passed" for case in cases)
        jobs = [job for case in cases for job in case.get("jobs", [])]
        self.report["summary"] = {
            "passed": passed,
            "failed": len(cases) - passed,
            "total": len(cases),
            "verifiedJobs": len(jobs),
            "succeededJobs": sum(job["status"] == "SUCCEEDED" for job in jobs),
            "failedJobs": sum(job["status"] == "FAILED" for job in jobs),
            "verifiedOutputObjects": sum(len(job["outputs"]) for job in jobs),
            "observedRenditionAttempts": sum(job["attempts"] for job in jobs),
        }
        self.report["status"] = (
            "passed"
            if cases and passed == len(cases) and not self.report.get("error")
            else "failed"
        )
        self.write()
        if self.http:
            self.http.close()
        if self.api:
            self.api.close()
        print(f"[integration] Results: {RESULTS}", flush=True)
        return 0 if self.report["status"] == "passed" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--case", choices=CASES, help="Run one case while diagnosing a local deployment"
    )
    parser.add_argument(
        "--timeout-seconds", type=int, default=480, help="Global deadline, at most 480 seconds"
    )
    args = parser.parse_args()
    if not 1 <= args.timeout_seconds <= 480:
        parser.error("--timeout-seconds must be between 1 and 480")
    suite = Integration(args.timeout_seconds)
    suite.report["requestedCases"] = [args.case] if args.case else list(CASES)
    signal.signal(signal.SIGALRM, alarm_handler)
    signal.setitimer(signal.ITIMER_REAL, max(0.001, suite.deadline - time.monotonic()))
    try:
        suite.setup()
        for name in (args.case,) if args.case else CASES:
            suite.run_case(name)
        suite.case_deadline = suite.deadline
        suite.report["finalMetrics"] = suite.request("GET", "/api/metrics")
    except Exception as error:
        suite.report["error"] = f"{type(error).__name__}: {error}"
        traceback.print_exc()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
    return suite.finish()


if __name__ == "__main__":
    raise SystemExit(main())
