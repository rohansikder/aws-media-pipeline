"""One bounded FFmpeg rendition task, suitable for an ECS container."""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import boto3
from botocore.config import Config as BotoConfig

MAX_SOURCE_BYTES = 100 * 1024 * 1024
MAX_DURATION_SECONDS = 120.0
MAX_FRAME_PIXELS = 7680 * 4320
PROCESS_TIMEOUT_SECONDS = 120
ALLOWED_RENDITIONS = {"360p", "720p", "thumbnail"}
INPUT_FORMATS = "mov,mp4,m4a,3gp,3g2,mj2,matroska,webm,avi,mpegts"


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class WorkerConfig:
    job_id: str
    object_key: str
    source_bucket: str
    output_bucket: str
    jobs_table: str
    rendition: str
    region: str = "eu-west-1"
    endpoint_url: str | None = None

    @classmethod
    def from_env(cls) -> WorkerConfig:
        required = [
            "JOB_ID",
            "OBJECT_KEY",
            "SOURCE_BUCKET",
            "OUTPUT_BUCKET",
            "JOBS_TABLE",
            "RENDITION",
        ]
        missing = [name for name in required if not os.environ.get(name, "").strip()]
        if missing:
            raise ValueError("Missing required worker settings: " + ", ".join(missing))
        rendition = os.environ["RENDITION"]
        if rendition not in ALLOWED_RENDITIONS:
            raise ValueError("RENDITION must be 360p, 720p or thumbnail")
        return cls(
            job_id=os.environ["JOB_ID"],
            object_key=os.environ["OBJECT_KEY"],
            source_bucket=os.environ["SOURCE_BUCKET"],
            output_bucket=os.environ["OUTPUT_BUCKET"],
            jobs_table=os.environ["JOBS_TABLE"],
            rendition=rendition,
            region=os.environ.get("AWS_DEFAULT_REGION")
            or os.environ.get("AWS_REGION")
            or "eu-west-1",
            endpoint_url=os.environ.get("AWS_ENDPOINT_URL") or None,
        )


@dataclass(frozen=True)
class MediaInfo:
    duration: float
    width: int
    height: int
    video_stream_index: int


def log(event: str, config: WorkerConfig | None = None, **fields: Any) -> None:
    record = {"timestamp": utc_now(), "event": event}
    if config is not None:
        record.update(jobId=config.job_id, rendition=config.rendition)
    record.update(fields)
    print(json.dumps(record, default=str, separators=(",", ":")), flush=True)


def _event(config: WorkerConfig, event_type: str, message: str, attempt: int) -> dict[str, Any]:
    return {
        "timestamp": utc_now(),
        "type": event_type,
        "message": message,
        "rendition": config.rendition,
        "attempt": attempt,
    }


def begin_attempt(table: Any, config: WorkerConfig) -> tuple[int, dict[str, Any]]:
    """Increment both counters atomically without replacing another worker's map."""
    table.update_item(
        Key={"jobId": config.job_id},
        ConditionExpression="attribute_exists(jobId)",
        UpdateExpression=(
            "SET attemptCounts = if_not_exists(attemptCounts, :empty_map), "
            "#outputs = if_not_exists(#outputs, :empty_map), "
            "#events = if_not_exists(#events, :empty_list), "
            "attempts = if_not_exists(attempts, :zero)"
        ),
        ExpressionAttributeNames={"#outputs": "outputs", "#events": "events"},
        ExpressionAttributeValues={":empty_map": {}, ":empty_list": [], ":zero": 0},
    )
    result = table.update_item(
        Key={"jobId": config.job_id},
        ConditionExpression="attribute_exists(jobId)",
        UpdateExpression=(
            "SET attemptCounts.#rendition = if_not_exists(attemptCounts.#rendition, :zero) + :one, "
            "attempts = if_not_exists(attempts, :zero) + :one"
        ),
        ExpressionAttributeNames={"#rendition": config.rendition},
        ExpressionAttributeValues={":zero": 0, ":one": 1},
        ReturnValues="ALL_NEW",
    )
    job = result["Attributes"]
    attempt = int(job["attemptCounts"][config.rendition])
    append_event(
        table, config, "worker_started", f"Started {config.rendition} attempt {attempt}", attempt
    )
    return attempt, job


def append_event(
    table: Any, config: WorkerConfig, event_type: str, message: str, attempt: int
) -> None:
    table.update_item(
        Key={"jobId": config.job_id},
        ConditionExpression="attribute_exists(jobId)",
        UpdateExpression="SET #events = list_append(if_not_exists(#events, :empty), :new)",
        ExpressionAttributeNames={"#events": "events"},
        ExpressionAttributeValues={
            ":empty": [],
            ":new": [_event(config, event_type, message, attempt)],
        },
    )


def record_output(table: Any, config: WorkerConfig, output: dict[str, Any], attempt: int) -> None:
    table.update_item(
        Key={"jobId": config.job_id},
        ConditionExpression=(
            "attribute_exists(jobId) AND attemptCounts.#rendition = :attempt AND #status = :running"
        ),
        UpdateExpression=(
            "SET #outputs.#rendition = :output, "
            "#events = list_append(if_not_exists(#events, :empty), :new)"
        ),
        ExpressionAttributeNames={
            "#outputs": "outputs",
            "#rendition": config.rendition,
            "#events": "events",
            "#status": "status",
        },
        ExpressionAttributeValues={
            ":output": output,
            ":attempt": attempt,
            ":running": "RUNNING",
            ":empty": [],
            ":new": [
                _event(
                    config,
                    "worker_completed",
                    f"Completed {config.rendition} attempt {attempt}",
                    attempt,
                )
            ],
        },
    )


def download_source(s3: Any, config: WorkerConfig, destination: Path) -> None:
    """Bound bytes while streaming, even if the object changes after submission."""
    response = s3.get_object(Bucket=config.source_bucket, Key=config.object_key)
    body = response["Body"]
    try:
        declared_size = int(response.get("ContentLength", 0))
        if declared_size <= 0 or declared_size > MAX_SOURCE_BYTES:
            raise ValueError("Input must be nonempty and no larger than 100 MiB")
        total = 0
        with destination.open("wb") as file:
            for chunk in body.iter_chunks(chunk_size=1024 * 1024):
                total += len(chunk)
                if total > MAX_SOURCE_BYTES:
                    raise ValueError("Input exceeds the 100 MiB limit")
                file.write(chunk)
        if total != declared_size:
            raise ValueError("Input download was incomplete")
    finally:
        body.close()


def _run(command: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(
            f"{Path(command[0]).name} exceeded its {timeout}-second timeout"
        ) from error
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or "Media processing failed").strip()[-1200:]
        raise RuntimeError(f"{Path(command[0]).name} failed: {detail}") from error


def probe_media(source: Path) -> MediaInfo:
    result = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-protocol_whitelist",
            "file,pipe",
            "-format_whitelist",
            INPUT_FORMATS,
            "-show_entries",
            "format=duration:stream=index,codec_type,width,height,duration:stream_disposition=attached_pic",
            "-of",
            "json",
            str(source),
        ],
        timeout=15,
    )
    info = json.loads(result.stdout)
    video = next(
        (
            stream
            for stream in info.get("streams", [])
            if stream.get("codec_type") == "video"
            and not stream.get("disposition", {}).get("attached_pic")
        ),
        None,
    )
    if video is None:
        raise ValueError("Input must contain a video stream")
    durations = []
    for value in (info.get("format", {}).get("duration"), video.get("duration")):
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number) and number > 0:
            durations.append(number)
    if not durations:
        raise ValueError("Input video must have a known positive duration")
    duration = max(durations)
    if duration > MAX_DURATION_SECONDS:
        raise ValueError("Input video duration exceeds 120 seconds")
    width, height = int(video.get("width", 0)), int(video.get("height", 0))
    if min(width, height) < 2 or max(width, height) > 8192 or width * height > MAX_FRAME_PIXELS:
        raise ValueError("Input video dimensions exceed the supported 8K frame budget")
    return MediaInfo(
        duration=duration, width=width, height=height, video_stream_index=int(video["index"])
    )


def ffmpeg_command(source: Path, destination: Path, rendition: str, media: MediaInfo) -> list[str]:
    if rendition not in ALLOWED_RENDITIONS:
        raise ValueError("Unsupported rendition")
    max_width, max_height = (1280, 720) if rendition == "720p" else (640, 360)
    # Fit display aspect ratio, including non-square source pixels, into an even
    # square-pixel frame. Bounds apply before scaling, so unusual SAR values
    # cannot create a huge intermediate image. Small inputs are not enlarged.
    scale = (
        f"scale=w='max(2,trunc(min({max_width},min(iw*sar,{max_height}*dar))/2)*2)':"
        f"h='max(2,trunc(min({max_height},min(ih,{max_width}/dar))/2)*2)',setsar=1"
    )
    command = [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-protocol_whitelist",
        "file,pipe",
        "-format_whitelist",
        INPUT_FORMATS,
    ]
    if rendition == "thumbnail":
        command += ["-ss", f"{min(1.0, media.duration / 2):.3f}"]
    command += [
        "-i",
        str(source),
        "-map",
        f"0:{media.video_stream_index}",
        "-vf",
        scale,
        "-map_metadata",
        "-1",
    ]
    if rendition == "thumbnail":
        command += [
            "-frames:v",
            "1",
            "-an",
            "-sn",
            "-dn",
            "-c:v",
            "mjpeg",
            "-q:v",
            "3",
            "-threads",
            "1",
            "-update",
            "1",
        ]
    else:
        command += [
            "-map",
            "0:a:0?",
            "-t",
            str(int(MAX_DURATION_SECONDS)),
            "-sn",
            "-dn",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "25",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            "-ac",
            "2",
            "-threads",
            "2",
            "-filter_threads",
            "1",
            "-movflags",
            "+faststart",
        ]
    return command + [str(destination)]


def emit_metrics(cloudwatch: Any, config: WorkerConfig, succeeded: bool, elapsed: float) -> None:
    try:
        dimensions = [{"Name": "Rendition", "Value": config.rendition}]
        cloudwatch.put_metric_data(
            Namespace="MediaPipeline",
            MetricData=[
                {
                    "MetricName": "RenditionSucceeded" if succeeded else "RenditionFailed",
                    "Value": 1,
                    "Unit": "Count",
                    "Dimensions": dimensions,
                },
                {
                    "MetricName": "ProcessingDuration",
                    "Value": elapsed,
                    "Unit": "Seconds",
                    "Dimensions": dimensions,
                },
            ],
        )
    except Exception as error:
        # Telemetry availability must not change the task's processing result.
        log("metric_delivery_failed", config, error=str(error))


def run_worker(config: WorkerConfig | None = None) -> int:
    started = time.monotonic()
    table = None
    cloudwatch = None
    attempt = 0
    try:
        config = config or WorkerConfig.from_env()
        client_config = BotoConfig(
            connect_timeout=5,
            read_timeout=30,
            retries={"max_attempts": 3, "mode": "standard"},
            s3={"addressing_style": "path"},
        )
        session = boto3.session.Session(region_name=config.region)
        arguments = {"endpoint_url": config.endpoint_url, "config": client_config}
        s3 = session.client("s3", **arguments)
        table = session.resource("dynamodb", **arguments).Table(config.jobs_table)
        cloudwatch = session.client(
            "cloudwatch",
            endpoint_url=config.endpoint_url,
            config=BotoConfig(
                connect_timeout=1,
                read_timeout=1,
                retries={"total_max_attempts": 1, "mode": "standard"},
            ),
        )
        attempt, job = begin_attempt(table, config)
        log("worker_started", config, attempt=attempt)
        if job.get("scenario") == "retry" and config.rendition == "360p" and attempt == 1:
            raise RuntimeError(
                "Controlled retry scenario: first 360p attempt fails before processing"
            )
        with tempfile.TemporaryDirectory(prefix="media-pipeline-") as directory:
            source = Path(directory) / "source"
            extension, content_type = (
                ("jpg", "image/jpeg") if config.rendition == "thumbnail" else ("mp4", "video/mp4")
            )
            destination = Path(directory) / f"output.{extension}"
            download_source(s3, config, source)
            media = probe_media(source)
            log(
                "input_validated",
                config,
                attempt=attempt,
                duration=media.duration,
                width=media.width,
                height=media.height,
            )
            _run(
                ffmpeg_command(source, destination, config.rendition, media),
                timeout=PROCESS_TIMEOUT_SECONDS,
            )
            if not destination.is_file() or destination.stat().st_size == 0:
                raise RuntimeError("FFmpeg did not produce a nonempty output")
            key = f"jobs/{config.job_id}/{config.rendition}/attempt-{attempt}.{extension}"
            s3.upload_file(
                str(destination),
                config.output_bucket,
                key,
                ExtraArgs={
                    "ContentType": content_type,
                    "Metadata": {"job-id": config.job_id, "rendition": config.rendition},
                },
            )
            output = {
                "key": key,
                "contentType": content_type,
                "sizeBytes": destination.stat().st_size,
            }
            record_output(table, config, output, attempt)
        elapsed = time.monotonic() - started
        log("worker_completed", config, attempt=attempt, elapsedSeconds=round(elapsed, 3), **output)
        emit_metrics(cloudwatch, config, True, elapsed)
        return 0
    except Exception as error:
        log("worker_failed", config, attempt=attempt, error=str(error))
        if table is not None and config is not None and attempt:
            try:
                append_event(table, config, "worker_failed", str(error)[:1500], attempt)
            except Exception as event_error:
                log("failure_event_delivery_failed", config, error=str(event_error))
        if cloudwatch is not None and config is not None:
            emit_metrics(cloudwatch, config, False, time.monotonic() - started)
        return 1


if __name__ == "__main__":
    sys.exit(run_worker())
