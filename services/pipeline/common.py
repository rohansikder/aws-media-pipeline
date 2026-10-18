"""Shared AWS clients and structured operational events."""

import json
import logging
import os
from datetime import UTC, datetime
from functools import lru_cache

import boto3
from botocore.config import Config

LOGGER = logging.getLogger("pipeline")
logging.basicConfig(level=logging.INFO, format="%(message)s")


def now():
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def environment():
    return os.getenv("ENVIRONMENT", "local" if os.getenv("AWS_ENDPOINT_URL") else "aws")


@lru_cache(maxsize=24)
def aws(service, public=False):
    endpoint = os.getenv("PUBLIC_AWS_ENDPOINT_URL") if public else os.getenv("AWS_ENDPOINT_URL")
    return boto3.client(
        service,
        region_name=os.getenv("AWS_DEFAULT_REGION") or os.getenv("AWS_REGION", "eu-west-1"),
        endpoint_url=endpoint or None,
        config=Config(
            retries={"total_max_attempts": 1 if service == "cloudwatch" else 4, "mode": "standard"},
            connect_timeout=1 if service == "cloudwatch" else 5,
            read_timeout=1 if service == "cloudwatch" else 15,
            signature_version="s3v4" if service == "s3" else None,
            s3={"addressing_style": "path"},
        ),
    )


def event(kind, message, **fields):
    return {"timestamp": now(), "type": kind, "message": message, **fields}


def log(kind, **fields):
    LOGGER.info(json.dumps({"timestamp": now(), "event": kind, **fields}, default=str))


def metric(name, value=1, unit="Count"):
    try:
        aws("cloudwatch").put_metric_data(
            Namespace="MediaPipeline",
            MetricData=[{"MetricName": name, "Value": value, "Unit": unit}],
        )
    except Exception as exc:
        log("metric_delivery_failed", metric=name, error=str(exc))
