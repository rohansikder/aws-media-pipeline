"""Exercise retries at the SQS boundary without calling live AWS services."""

import json
from unittest.mock import Mock
from urllib.parse import quote_plus

import pytest
from botocore.exceptions import ClientError
from pipeline import dispatch

JOB_ID = "a" * 32
BUCKET = "test-uploads"
KEY = f"uploads/{JOB_ID}/a clip.mp4"
STATE_MACHINE_ARN = "arn:aws:states:us-east-1:123456789012:stateMachine:media"
EXECUTION_ARN = f"arn:aws:states:us-east-1:123456789012:execution:media:job-{JOB_ID}"
ARN_PUBLICATION_CONDITION = "attribute_exists(jobId) AND attribute_not_exists(executionArn)"


def client_error(code, operation):
    return ClientError({"Error": {"Code": code}}, operation)


def notification(*, bucket=BUCKET, key=KEY, event_name="ObjectCreated:Put"):
    return {
        "eventName": event_name,
        "s3": {"bucket": {"name": bucket}, "object": {"key": quote_plus(key)}},
    }


def message(message_id, *records):
    return {"messageId": message_id, "body": json.dumps({"Records": list(records)})}


@pytest.fixture
def dependencies(monkeypatch):
    monkeypatch.setenv("INPUT_BUCKET", BUCKET)
    monkeypatch.setenv("STATE_MACHINE_ARN", STATE_MACHINE_ARN)
    monkeypatch.setattr(dispatch, "metric", Mock())
    jobs = Mock(return_value={"jobId": JOB_ID, "objectKey": KEY, "status": "RUNNING"})
    update = Mock()
    workflows = Mock()
    workflows.start_execution.return_value = {"executionArn": EXECUTION_ARN}
    monkeypatch.setattr(dispatch.storage, "get_job", jobs)
    monkeypatch.setattr(dispatch.storage, "update_job", update)
    monkeypatch.setattr(dispatch, "aws", Mock(return_value=workflows))
    return workflows, jobs, update


def test_duplicate_notifications_share_one_named_execution(dependencies):
    workflows, _, update = dependencies
    workflows.start_execution.side_effect = [
        {"executionArn": EXECUTION_ARN},
        client_error("ExecutionAlreadyExists", "StartExecution"),
    ]
    update.side_effect = [None, client_error("ConditionalCheckFailedException", "UpdateItem")]

    result = dispatch.handler({"Records": [
        message("first", notification()), message("redelivery", notification()),
    ]}, None)

    assert result == {"batchItemFailures": []}
    first, second = workflows.start_execution.call_args_list
    assert first.kwargs == second.kwargs
    assert first.kwargs["name"] == f"job-{JOB_ID}"
    assert json.loads(first.kwargs["input"]) == {
        "jobId": JOB_ID, "objectKey": KEY, "bucket": BUCKET,
    }
    # Dispatch metadata must never regress an already-running or completed job.
    assert update.call_count == 2
    for call in update.call_args_list:
        assert call.args == (JOB_ID, {"executionArn": EXECUTION_ARN})
        assert call.kwargs == {"condition": ARN_PUBLICATION_CONDITION}
    assert [call.args for call in dispatch.metric.call_args_list] == [
        ("JobsDispatched",), ("DuplicateEvents",),
    ]


def test_partial_batch_retries_only_failed_sqs_messages(dependencies):
    workflows, _, update = dependencies
    workflows.start_execution.side_effect = [
        ClientError({"Error": {"Code": "ThrottlingException"}}, "StartExecution"),
        {"executionArn": EXECUTION_ARN},
    ]

    result = dispatch.handler({"Records": [
        message("transient-failure", notification()),
        {"messageId": "invalid-json", "body": "{"},
        message("successful", notification()),
    ]}, None)

    assert result == {"batchItemFailures": [
        {"itemIdentifier": "transient-failure"}, {"itemIdentifier": "invalid-json"},
    ]}
    update.assert_called_once_with(
        JOB_ID, {"executionArn": EXECUTION_ARN}, condition=ARN_PUBLICATION_CONDITION,
    )


def test_completed_execution_arn_is_recovered_after_metadata_write_failure(dependencies):
    workflows, jobs, update = dependencies
    workflows.start_execution.side_effect = [
        {"executionArn": EXECUTION_ARN}, client_error("ExecutionAlreadyExists", "StartExecution"),
    ]
    update.side_effect = [client_error("ProvisionedThroughputExceededException", "UpdateItem"), None]

    first = dispatch.handler({"Records": [message("first", notification())]}, None)
    assert first == {"batchItemFailures": [{"itemIdentifier": "first"}]}
    dispatch.metric.assert_not_called()
    jobs.return_value["status"] = "SUCCEEDED"

    redelivery = dispatch.handler({"Records": [message("redelivery", notification())]}, None)

    assert redelivery == {"batchItemFailures": []}
    update.assert_called_with(
        JOB_ID, {"executionArn": EXECUTION_ARN}, condition=ARN_PUBLICATION_CONDITION,
    )
    assert jobs.return_value["status"] == "SUCCEEDED"
    dispatch.metric.assert_called_once_with("JobsDispatched")


def test_running_execution_duplicate_does_not_double_count_dispatches(dependencies):
    workflows, _, update = dependencies
    # STANDARD workflows return the same execution ARN for identical input while running.
    update.side_effect = [None, client_error("ConditionalCheckFailedException", "UpdateItem")]
    result = dispatch.handler({"Records": [
        message("first", notification()), message("duplicate", notification()),
    ]}, None)
    assert result == {"batchItemFailures": []}
    assert workflows.start_execution.call_count == 2
    names = [call.args[0] for call in dispatch.metric.call_args_list]
    assert names.count("JobsDispatched") == 1
    assert names.count("DuplicateEvents") == 1


def test_nonconditional_metadata_failure_retries_only_affected_message(dependencies):
    _, _, update = dependencies
    update.side_effect = [client_error("InternalServerError", "UpdateItem"), None]
    result = dispatch.handler({"Records": [
        message("retry-write", notification()), message("published", notification()),
    ]}, None)
    assert result == {"batchItemFailures": [{"itemIdentifier": "retry-write"}]}
    dispatch.metric.assert_called_once_with("JobsDispatched")


@pytest.mark.parametrize("record", [
    notification(bucket="unexpected-bucket"),
    notification(key=f"uploads/{JOB_ID}/nested/clip.mp4"),
    notification(key="uploads/not-a-job/clip.mp4"),
    notification(key=f"outputs/{JOB_ID}/clip.mp4"),
])
def test_untrusted_object_locations_never_start_a_workflow(dependencies, record):
    workflows, _, update = dependencies
    result = dispatch.handler({"Records": [message("rejected", record)]}, None)
    assert result == {"batchItemFailures": [{"itemIdentifier": "rejected"}]}
    workflows.start_execution.assert_not_called()
    update.assert_not_called()


@pytest.mark.parametrize("reservation", [None, {"objectKey": "uploads/other.mp4"}])
def test_upload_must_match_its_reserved_object_key(dependencies, reservation):
    workflows, jobs, _ = dependencies
    jobs.return_value = reservation
    result = dispatch.handler({"Records": [message("unreserved", notification())]}, None)
    assert result == {"batchItemFailures": [{"itemIdentifier": "unreserved"}]}
    workflows.start_execution.assert_not_called()


def test_test_events_and_non_creation_events_are_acknowledged(dependencies):
    workflows, _, _ = dependencies
    result = dispatch.handler({"Records": [
        {"messageId": "test", "body": json.dumps({"Event": "s3:TestEvent"})},
        message("removal", notification(event_name="ObjectRemoved:Delete")),
    ]}, None)
    assert result == {"batchItemFailures": []}
    workflows.start_execution.assert_not_called()


@pytest.mark.parametrize("body", [{}, {"Records": []}, {"Records": {"bad": "shape"}}])
def test_malformed_notifications_are_not_silently_dropped(dependencies, body):
    result = dispatch.handler({"Records": [{
        "messageId": "malformed", "body": json.dumps(body),
    }]}, None)
    assert result == {"batchItemFailures": [{"itemIdentifier": "malformed"}]}
