"""Load non-secret local resource names from Terraform outputs."""

import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(target="local"):
    terraform = os.getenv("TERRAFORM", "terraform")
    output = json.loads(
        subprocess.check_output(
            [
                terraform,
                "-chdir=" + str(ROOT / "infra" / "environments" / target),
                "output",
                "-json",
            ]
        )
    )
    output = {key: value["value"] for key, value in output.items()}
    # These credentials are intentionally local placeholders, never AWS credentials.
    local_environment = {
        "AWS_ACCESS_KEY_ID": "000000000000",
        "AWS_SECRET_ACCESS_KEY": "test",
        "AWS_DEFAULT_REGION": "eu-west-1",
        "AWS_ENDPOINT_URL": "http://localhost:4566",
        "PUBLIC_AWS_ENDPOINT_URL": "http://localhost:4566",
        "ENVIRONMENT": "local",
        "ENABLE_SCENARIOS": "true",
        "AWS_EC2_METADATA_DISABLED": "true",
    }
    if target == "local":
        os.environ.update(local_environment)
    else:
        # Keep the user's normal AWS credentials/profile chain for real AWS.
        os.environ.pop("AWS_ENDPOINT_URL", None)
        os.environ.pop("PUBLIC_AWS_ENDPOINT_URL", None)
        os.environ.update({"ENVIRONMENT": "aws", "ENABLE_SCENARIOS": "false"})
        os.environ["AWS_DEFAULT_REGION"] = output.get("config", {}).get("region", "eu-west-1")
    mapping = {
        "input_bucket": "INPUT_BUCKET",
        "output_bucket": "OUTPUT_BUCKET",
        "jobs_table": "JOBS_TABLE",
        "ingest_queue_url": "INGEST_QUEUE_URL",
        "dlq_url": "DLQ_URL",
        "failure_queue_url": "FAILURE_QUEUE_URL",
        "state_machine_arn": "STATE_MACHINE_ARN",
    }
    for source, destination in mapping.items():
        os.environ[destination] = str(output[source])
    return output


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--environment", choices=["local", "aws"], default="local")
    load(parser.parse_args().environment)
    import uvicorn

    uvicorn.run("pipeline.api:app", host="127.0.0.1", port=8000)
