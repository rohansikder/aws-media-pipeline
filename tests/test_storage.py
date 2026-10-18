"""Check the DynamoDB concurrency contract with botocore's API validator."""

from unittest.mock import Mock

import pytest
from botocore.exceptions import ClientError
from botocore.stub import Stubber
from pipeline import storage
from pipeline.common import aws


def test_duplicate_reservations_are_conditionally_rejected(monkeypatch):
    monkeypatch.setenv("JOBS_TABLE", "jobs")
    client = aws("dynamodb")
    monkeypatch.setattr(storage, "aws", Mock(return_value=client))
    job = {"jobId": "a" * 32, "status": "UPLOADING", "sizeBytes": 1024}
    with Stubber(client) as stub:
        stub.add_client_error(
            "put_item", service_error_code="ConditionalCheckFailedException",
            expected_params={
                "TableName": "jobs", "Item": storage.marshal(job),
                "ConditionExpression": "attribute_not_exists(jobId)",
            },
        )
        with pytest.raises(ClientError) as error:
            storage.create_job(job)
        assert error.value.response["Error"]["Code"] == "ConditionalCheckFailedException"
        stub.assert_no_pending_responses()


def test_field_updates_do_not_replace_concurrent_rendition_outputs(monkeypatch):
    monkeypatch.setenv("JOBS_TABLE", "jobs")
    client = Mock()
    monkeypatch.setattr(storage, "aws", Mock(return_value=client))
    storage.update_job("a" * 32, {"status": "RUNNING"}, "workflow_started", "Started")
    request = client.update_item.call_args.kwargs
    assert "outputs" not in request["ExpressionAttributeNames"].values()
    assert request["ConditionExpression"] == "attribute_exists(jobId)"
    assert "list_append(if_not_exists(#events, :empty), :events)" in request["UpdateExpression"]


def test_status_transition_checks_expected_state_atomically(monkeypatch):
    monkeypatch.setenv("JOBS_TABLE", "jobs")
    client = Mock()
    monkeypatch.setattr(storage, "aws", Mock(return_value=client))
    storage.update_job("a" * 32, {"status": "QUEUED"}, expected_status="UPLOADING")
    request = client.update_item.call_args.kwargs
    assert "#current = :expected" in request["ConditionExpression"]
    assert request["ExpressionAttributeNames"]["#current"] == "status"
    assert storage.unmarshal(request["ExpressionAttributeValues"])[":expected"] == "UPLOADING"


def test_fractional_metrics_round_trip_as_json_compatible_numbers():
    record = {"durationSeconds": 1.125, "attempts": 2, "nested": {"samples": [0.5, 3]}}
    result = storage.unmarshal(storage.marshal(record))
    assert result == record
    assert isinstance(result["durationSeconds"], float)
    assert isinstance(result["attempts"], int)
