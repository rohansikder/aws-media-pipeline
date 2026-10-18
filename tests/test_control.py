"""Validate terminal workflow status rather than only the happy-path payload."""

import json
from unittest.mock import Mock

import pytest
from pipeline import control

JOB_ID = "a" * 32
PAYLOAD = {"jobId": JOB_ID, "objectKey": f"uploads/{JOB_ID}/clip.mp4"}


@pytest.fixture
def lifecycle(monkeypatch):
    job = {
        "jobId": JOB_ID, "createdAt": "2026-01-01T00:00:00+00:00",
        "startedAt": "2026-01-01T00:00:05+00:00",
        "outputs": {"360p": {}, "720p": {}, "thumbnail": {}},
    }
    updates, metrics, queue = Mock(), Mock(), Mock()
    monkeypatch.setattr(control.storage, "get_job", Mock(return_value=job))
    monkeypatch.setattr(control.storage, "update_job", updates)
    monkeypatch.setattr(control, "metric", metrics)
    monkeypatch.setattr(control, "now", lambda: "2026-01-01T00:00:15+00:00")
    monkeypatch.setattr(control, "aws", Mock(return_value=queue))
    monkeypatch.setenv("FAILURE_QUEUE_URL", "http://queue/failures")
    return job, updates, metrics, queue


def test_success_requires_every_rendition_and_keeps_existing_outputs(lifecycle):
    _, updates, metrics, queue = lifecycle
    assert control.handler({"action": "complete", "job": PAYLOAD}, None) == PAYLOAD
    fields = updates.call_args.args[1]
    assert fields == {
        "status": "SUCCEEDED", "completedAt": "2026-01-01T00:00:15+00:00",
        "durationSeconds": 10.0,
    }
    metrics.assert_any_call("JobsSucceeded")
    queue.send_message.assert_not_called()


@pytest.mark.parametrize("missing", ["360p", "720p", "thumbnail"])
def test_partial_outputs_cannot_be_marked_successful(lifecycle, missing):
    job, updates, metrics, queue = lifecycle
    del job["outputs"][missing]
    with pytest.raises(ValueError, match="all three output renditions"):
        control.handler({"action": "complete", "job": PAYLOAD}, None)
    updates.assert_not_called()
    metrics.assert_not_called()
    queue.send_message.assert_not_called()


def test_failure_persists_status_and_publishes_bounded_diagnostics(lifecycle):
    job, updates, metrics, queue = lifecycle
    job["outputs"] = {"360p": {"key": "already-published.mp4"}}
    error = "transcode failed: " + "x" * 4000
    control.handler({"action": "fail", "job": PAYLOAD, "error": error}, None)
    fields = updates.call_args.args[1]
    assert fields["status"] == "FAILED"
    assert fields["error"] == error[:3000]
    assert "outputs" not in fields
    metrics.assert_any_call("JobsFailed")
    assert queue.send_message.call_args.kwargs["QueueUrl"] == "http://queue/failures"
    assert json.loads(queue.send_message.call_args.kwargs["MessageBody"]) == {
        "jobId": JOB_ID, "error": error[:3000],
    }


def test_workflow_cannot_transition_an_unknown_job(lifecycle, monkeypatch):
    _, updates, _, _ = lifecycle
    monkeypatch.setattr(control.storage, "get_job", Mock(return_value=None))
    with pytest.raises(ValueError, match="reservation does not exist"):
        control.handler({"action": "start", "job": PAYLOAD}, None)
    updates.assert_not_called()


def test_unknown_action_does_not_mutate_a_job(lifecycle):
    _, updates, _, _ = lifecycle
    with pytest.raises(ValueError, match="Unknown workflow action"):
        control.handler({"action": "delete-everything", "job": PAYLOAD}, None)
    updates.assert_not_called()
