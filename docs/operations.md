# Operating the media pipeline

The job record, workflow history, container logs, and two failure queues describe different stages of the same request. Follow the job ID from the dashboard before replaying work.

## Start and verify locally

Run `make up` to build the worker, start Floci, and apply the local Terraform resources. Run `make serve` in a second terminal and open `http://localhost:8000`. The API loads resource names from Terraform outputs through `scripts/local_env.py`; its signed upload and download URLs point to the local S3 endpoint.

`make integration` provisions the environment and runs the end-to-end checks. To rerun checks against an environment that is already running:

```bash
.venv/bin/python scripts/integration.py
```

The harness uses the FastAPI application for reservations and scenario requests, performs a real HTTP PUT to a signed S3 URL, and waits for S3 notifications, SQS delivery, the dispatch Lambda, Step Functions, and ECS containers. A repeated PUT to the same write-once upload URL must return HTTP 412 while leaving the original source bytes unchanged. It reads job state from DynamoDB and verifies the resulting objects in S3. It does not invoke processing handlers directly or manufacture completed jobs.

Six cases cover a normal upload, duplicate notifications, a controlled first-attempt failure, corrupt input, a five-job burst, and a malformed intake message reaching the dispatch dead-letter queue. Successful jobs must publish exactly two MP4 files and one JPEG. Object signatures, content types, sizes, metadata, and workflow history are checked; when `ffprobe` is installed on the host, the harness also checks codecs, dimensions, and video duration. Download links are exercised for the normal upload.

The harness writes actual observations to `build/integration-results.json` and exits unsuccessfully if a check fails. It enforces a 180-second budget per case and an eight-minute global deadline. `--case retry`, for example, runs one case while diagnosing a deployment. It leaves job records, media objects, and diagnostic queue messages in place; repeated runs add data. It does not purge queues or remove earlier work.

## Identify a stalled stage

| Observation | Inspect next |
| --- | --- |
| `UPLOADING` with no input object | The upload PUT response, browser network request, signed URL expiry, and input bucket/key. A reservation alone does not submit media. |
| Input object exists; no execution ARN | S3 bucket notifications, intake queue depth, dispatch Lambda logs and event-source mapping, then the intake DLQ. |
| `RUNNING` and no outputs yet | Step Functions history, ECS task status and exit code, then worker logs for the job ID and rendition. |
| Some renditions finished while another is retrying | Per-rendition attempt counts and worker events. A successful rendition does not mean the whole job is complete. |
| `FAILED` | The job error, failed Step Functions state, worker logs, and processing-failure notification. |
| `SUCCEEDED` but an output will not play | The object key and content type, fresh signed download URL, S3 metadata, and media validity. Signed URLs expire after 15 minutes. |

`GET /api/jobs/{jobId}/history` returns application events and, once dispatch has started an execution, Step Functions history. Application events distinguish attempts by rendition; workflow history explains retries and the terminal transition. The deterministic execution name is `job-{jobId}`. Repeated delivery of the same upload notification must not create another execution for that job. Output keys include the rendition and attempt, such as `jobs/{jobId}/360p/attempt-2.mp4`, so a stale attempt cannot overwrite another attempt's media. The job record identifies the published output for each rendition.

The intake queue visibility timeout and `maxReceiveCount` determine how long dispatch failures take to reach the DLQ. Read the deployed queue attributes instead of assuming immediate delivery. The integration result records those values and the observed elapsed seconds. Queue depths are approximate and include messages currently being processed.

## Recover dispatch failures

The **intake DLQ** contains messages the dispatch Lambda could not accept after SQS delivery attempts. This is different from a media job failing after dispatch.

1. Inspect the message body and dispatch logs. Check that the S3 notification references the expected input bucket, a valid upload key, an existing reservation, and an object that still exists.
2. Fix the underlying configuration or service failure before replaying a valid notification.
3. Use the local recovery action or `POST /api/dlq/redrive` to replay up to ten structurally valid notifications from the intake DLQ. A replay sends the original body to the intake queue and deletes that DLQ message only after the send succeeds. Existing execution names provide duplicate-dispatch protection.
4. Check the job and workflow history again. A successful API response means messages were replayed; it does not mean processing has completed.

Malformed bodies are deliberately left in the DLQ for inspection. The recovery endpoint skips invalid JSON and bodies without a nonempty `Records` field. This structural screen does not prove that a notification has a valid bucket, object key, reservation, or supported event. An unusable notification can fail dispatch again; correct its cause before sending a repaired notification through the normal intake path. Do not repeatedly redrive poison messages or clear the queue to hide a failure. The endpoint reads at most ten messages at a time, so messages that remain can also affect which later messages are inspected on a subsequent request.

The **processing-failure queue** contains job IDs and errors for workflows that exhausted processing retries. It is an operational notification queue, not a second upload queue, and the intake redrive action does not consume it. For corrupt or unsupported media, obtain a valid source and submit a new upload reservation. Inspect partial output objects before making a cleanup decision; a failed job may have siblings that completed before another rendition failed.

Reliability scenarios and redrive controls are enabled only in the local environment by default. Keep them disabled for a normal AWS deployment. Use authenticated operator access and the deployed IAM permissions for AWS diagnostics.

## Read measurements correctly

API and worker event timestamps use ISO 8601 UTC strings. DynamoDB `expiresAt` is an epoch value in **seconds**, used for retention rather than workflow timing. Job `durationSeconds` measures workflow processing from its recorded start to terminal state; it excludes the initial browser upload and time spent waiting before workflow start. Integration `elapsedSeconds` is harness wall-clock time and includes polling and verification for its case. Worker processing metrics also use seconds.

The dashboard reports actual retained job records, rendition counts, per-rendition retries, and recent completion buckets. Its throughput figure is successful jobs completed in the previous minute, not concurrent uploads or requests per second. Duration percentiles use successful jobs that have a duration; the API includes `sampleSize` and the bounded `historyLimit` of 1,000 job records. Queue depths are approximate. Historical data from previous runs remains visible unless the local environment is reset.

Local integration results describe this emulator, worker image, fixture, host, and run. They validate functional paths and observable recovery. They do not establish AWS capacity, production latency, account quotas, availability, or the correctness of AWS IAM enforcement. Use a separately documented workload on AWS to measure those properties.

## Emulator lifecycle and cleanup

Floci implements AWS-compatible APIs and launches Lambda and ECS workloads as local containers. Its scheduling, retries, network boundaries, task startup costs, and persistence behavior differ from managed AWS services. A restart can interrupt an active local workflow; do not assume an in-flight Step Functions execution will resume correctly after restarting Floci. Inspect or abort the interrupted execution, retain its diagnostics, and submit a new upload when the environment is healthy. A DynamoDB job left in `RUNNING` is evidence to investigate, not proof that a task survived the restart.

Use `docker compose logs floci` and the relevant worker-container logs when a local check fails. `make down` stops the local service. `make clean` destroys the local Terraform resources and removes Compose volumes; it deletes local jobs and media and should be used only when that data can be discarded. AWS teardown is separate: inspect the target environment and retained bucket contents before applying a destroy operation there.
