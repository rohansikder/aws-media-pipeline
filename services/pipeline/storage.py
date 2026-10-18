"""DynamoDB job records; mutations preserve concurrent rendition updates."""

import os
from decimal import Decimal

from boto3.dynamodb.types import TypeDeserializer, TypeSerializer

from pipeline.common import aws, event, now

SERIALIZER = TypeSerializer()
DESERIALIZER = TypeDeserializer()


def encode(value):
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: encode(v) for k, v in value.items()}
    if isinstance(value, list):
        return [encode(v) for v in value]
    return value


def marshal(value):
    return {k: SERIALIZER.serialize(encode(v)) for k, v in value.items()}


def clean(value):
    if isinstance(value, Decimal):
        return float(value) if value % 1 else int(value)
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [clean(v) for v in value]
    return value


def unmarshal(value):
    return clean({k: DESERIALIZER.deserialize(v) for k, v in value.items()})


def table():
    return os.environ["JOBS_TABLE"]


def create_job(job):
    aws("dynamodb").put_item(
        TableName=table(), Item=marshal(job), ConditionExpression="attribute_not_exists(jobId)"
    )
    return job


def get_job(job_id):
    item = (
        aws("dynamodb")
        .get_item(TableName=table(), Key=marshal({"jobId": job_id}), ConsistentRead=True)
        .get("Item")
    )
    return unmarshal(item) if item else None


def list_jobs():
    # A bounded operational view. The time-indexed production query path can be
    # introduced when the retained history grows beyond the demo's 1,000 jobs.
    response = aws("dynamodb").scan(TableName=table(), Limit=1000)
    return sorted(
        [unmarshal(item) for item in response.get("Items", [])],
        key=lambda job: job["createdAt"],
        reverse=True,
    )


def update_job(job_id, fields, kind=None, message=None, condition=None, expected_status=None):
    fields = {**fields, "updatedAt": now()}
    names, values, assignments = {}, {}, []
    for index, (key, value) in enumerate(fields.items()):
        names[f"#f{index}"] = key
        values[f":v{index}"] = value
        assignments.append(f"#f{index} = :v{index}")
    if kind:
        names["#events"] = "events"
        values[":empty"] = []
        values[":events"] = [event(kind, message or kind)]
        assignments.append("#events = list_append(if_not_exists(#events, :empty), :events)")
    if expected_status:
        names["#current"] = "status"
        values[":expected"] = expected_status
        condition = "attribute_exists(jobId) AND #current = :expected"
    kwargs = {
        "TableName": table(),
        "Key": marshal({"jobId": job_id}),
        "UpdateExpression": "SET " + ", ".join(assignments),
        "ExpressionAttributeNames": names,
        "ExpressionAttributeValues": marshal(values),
        "ConditionExpression": condition or "attribute_exists(jobId)",
    }
    return aws("dynamodb").update_item(**kwargs)
