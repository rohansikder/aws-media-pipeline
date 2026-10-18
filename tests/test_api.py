"""Validate public request boundaries and upload/status race behavior."""

import json
from unittest.mock import Mock

import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient
from pipeline import api

JOB_ID = "a" * 32


@pytest.fixture
def client():
    return TestClient(api.app)


@pytest.mark.parametrize("changes", [
    {"filename": "../clip.mp4"},
    {"filename": "folder/clip.mp4"},
    {"filename": r"folder\clip.mp4"},
    {"filename": "clip.exe"},
    {"contentType": "text/plain"},
    {"sizeBytes": 0},
    {"sizeBytes": 100 * 1024 * 1024 + 1},
])
def test_invalid_uploads_are_rejected_before_reserving_storage(client, monkeypatch, changes):
    reserve = Mock()
    monkeypatch.setattr(api.storage, "create_job", reserve)
    request = {"filename": "clip.mp4", "contentType": "video/mp4", "sizeBytes": 1024}
    response = client.post("/api/uploads", json={**request, **changes})
    assert response.status_code == 422
    reserve.assert_not_called()


def test_upload_url_is_scoped_to_reserved_key_type_size_and_expiry(client, monkeypatch):
    monkeypatch.setenv("INPUT_BUCKET", "input-bucket")
    s3 = Mock()
    s3.generate_presigned_url.return_value = "http://storage/scoped-signature"
    factory = Mock(return_value=s3)
    reserve = Mock(side_effect=lambda job: job)
    monkeypatch.setattr(api, "aws", factory)
    monkeypatch.setattr(api.storage, "create_job", reserve)

    response = client.post("/api/uploads", json={
        "filename": "my clip.mp4", "contentType": "video/mp4", "sizeBytes": 2048,
    })

    assert response.status_code == 201
    body = response.json()
    assert body["objectKey"] == f"uploads/{body['jobId']}/my clip.mp4"
    assert len(body["jobId"]) == 32
    factory.assert_called_once_with("s3", public=True)
    s3.generate_presigned_url.assert_called_once_with(
        "put_object", Params={"Bucket": "input-bucket", "Key": body["objectKey"],
                              "ContentType": "video/mp4", "ContentLength": 2048,
                              "IfNoneMatch": "*"},
        ExpiresIn=900,
    )
    assert body["headers"] == {"Content-Type": "video/mp4", "If-None-Match": "*"}
    assert reserve.call_args.args[0]["status"] == "UPLOADING"


@pytest.mark.parametrize("status", ["RUNNING", "SUCCEEDED", "FAILED"])
def test_upload_confirmation_never_downgrades_advanced_jobs(client, monkeypatch, status):
    job = {"jobId": JOB_ID, "status": status, "objectKey": "uploads/file.mp4"}
    monkeypatch.setenv("INPUT_BUCKET", "input-bucket")
    monkeypatch.setattr(api.storage, "get_job", Mock(return_value=job))
    update = Mock()
    monkeypatch.setattr(api.storage, "update_job", update)
    monkeypatch.setattr(api, "aws", Mock(return_value=Mock()))
    response = client.post(f"/api/jobs/{JOB_ID}/confirm")
    assert response.status_code == 200
    assert response.json()["status"] == status
    update.assert_not_called()


def test_confirmation_race_accepts_only_conditional_conflict(client, monkeypatch):
    uploading = {"jobId": JOB_ID, "status": "UPLOADING", "objectKey": "uploads/file.mp4"}
    running = {**uploading, "status": "RUNNING"}
    monkeypatch.setenv("INPUT_BUCKET", "input-bucket")
    monkeypatch.setattr(api.storage, "get_job", Mock(side_effect=[uploading, running]))
    update = Mock(side_effect=ClientError(
        {"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem",
    ))
    monkeypatch.setattr(api.storage, "update_job", update)
    monkeypatch.setattr(api, "aws", Mock(return_value=Mock()))

    response = client.post(f"/api/jobs/{JOB_ID}/confirm")

    assert response.status_code == 200
    assert response.json()["status"] == "RUNNING"
    assert update.call_args.kwargs["expected_status"] == "UPLOADING"


def test_confirmation_waits_for_actual_s3_object(client, monkeypatch):
    job = {"jobId": JOB_ID, "status": "UPLOADING", "objectKey": "uploads/file.mp4"}
    monkeypatch.setenv("INPUT_BUCKET", "input-bucket")
    monkeypatch.setattr(api.storage, "get_job", Mock(return_value=job))
    update = Mock()
    monkeypatch.setattr(api.storage, "update_job", update)
    s3 = Mock()
    s3.head_object.side_effect = ClientError({"Error": {"Code": "404"}}, "HeadObject")
    monkeypatch.setattr(api, "aws", Mock(return_value=s3))
    response = client.post(f"/api/jobs/{JOB_ID}/confirm")
    assert response.status_code == 409
    update.assert_not_called()


@pytest.mark.parametrize("path", ["/api/scenarios", "/api/dlq/redrive"])
def test_reliability_controls_are_disabled_by_default(client, monkeypatch, path):
    monkeypatch.delenv("ENABLE_SCENARIOS", raising=False)
    services = Mock()
    monkeypatch.setattr(api, "aws", services)
    response = client.post(path, json={"scenario": "duplicate"})
    assert response.status_code == 403
    services.assert_not_called()


def test_redrive_leaves_poison_messages_and_replays_valid_notifications(client, monkeypatch):
    monkeypatch.setenv("ENABLE_SCENARIOS", "true")
    monkeypatch.setenv("DLQ_URL", "http://queue/dead-letter")
    monkeypatch.setenv("INGEST_QUEUE_URL", "http://queue/ingest")
    valid = json.dumps({"Records": [{"eventName": "ObjectCreated:Put"}]})
    queue = Mock()
    queue.receive_message.return_value = {"Messages": [
        {"Body": "{", "ReceiptHandle": "invalid-json"},
        {"Body": "[]", "ReceiptHandle": "wrong-json-shape"},
        {"Body": json.dumps({"invalid": True}), "ReceiptHandle": "missing-records"},
        {"Body": valid, "ReceiptHandle": "valid-notification"},
    ]}
    monkeypatch.setattr(api, "aws", Mock(return_value=queue))

    response = client.post("/api/dlq/redrive")

    assert response.status_code == 200
    assert response.json()["redriven"] == 1
    queue.send_message.assert_called_once_with(QueueUrl="http://queue/ingest", MessageBody=valid)
    queue.delete_message.assert_called_once_with(
        QueueUrl="http://queue/dead-letter", ReceiptHandle="valid-notification",
    )


def test_failed_jobs_do_not_distort_successful_processing_latency(client, monkeypatch):
    monkeypatch.setattr(api.storage, "list_jobs", Mock(return_value=[
        {"status": "SUCCEEDED", "durationSeconds": 2.0, "attemptCounts": {"360p": 2}},
        {"status": "SUCCEEDED", "durationSeconds": 4.0},
        {"status": "FAILED", "durationSeconds": 1000.0},
        {"status": "RUNNING"},
    ]))
    monkeypatch.setattr(api, "queue_depth", Mock(return_value=0))
    response = client.get("/api/metrics")
    assert response.status_code == 200
    body = response.json()
    assert body["p50Seconds"] == 3.0
    assert body["p95Seconds"] == 3.9
    assert body["sampleSize"] == 2
    assert body["retryCount"] == 1
    assert body["active"] == 1
    assert body["successRate"] == 66.7
