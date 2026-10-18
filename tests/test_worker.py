"""Bound hostile input and subprocess failures before publishing outputs."""

import json
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest
from pipeline import worker


@pytest.fixture
def config():
    return worker.WorkerConfig(
        job_id="a" * 32, object_key="uploads/source.mp4", source_bucket="sources",
        output_bucket="outputs", jobs_table="jobs", rendition="360p",
    )


@pytest.mark.parametrize("declared,chunks,limit,error", [
    (9, [b"123456789"], 8, "no larger than"),
    (4, [b"1234", b"56789"], 8, "exceeds"),
    (5, [b"1234"], 8, "incomplete"),
])
def test_download_limits_and_incomplete_streams_close_the_body(
    monkeypatch, tmp_path, config, declared, chunks, limit, error,
):
    monkeypatch.setattr(worker, "MAX_SOURCE_BYTES", limit)
    body = Mock()
    body.iter_chunks.return_value = iter(chunks)
    s3 = Mock()
    s3.get_object.return_value = {"ContentLength": declared, "Body": body}
    with pytest.raises(ValueError, match=error):
        worker.download_source(s3, config, tmp_path / "source")
    body.close.assert_called_once()


@pytest.mark.parametrize("metadata,error", [
    ({"format": {"duration": "121"}, "streams": [
        {"index": 0, "codec_type": "video", "width": 640, "height": 360},
    ]}, "exceeds 120 seconds"),
    ({"format": {"duration": "1"}, "streams": [
        {"index": 0, "codec_type": "video", "width": 640, "height": 360, "duration": "121"},
    ]}, "exceeds 120 seconds"),
    ({"format": {"duration": "NaN"}, "streams": [
        {"index": 0, "codec_type": "video", "width": 640, "height": 360},
    ]}, "known positive duration"),
    ({"format": {"duration": "1"}, "streams": [
        {"index": 0, "codec_type": "video", "width": 20000, "height": 20000},
    ]}, "frame budget"),
    ({"format": {"duration": "1"}, "streams": [
        {"index": 0, "codec_type": "audio"},
    ]}, "video stream"),
])
def test_probe_rejects_unbounded_or_non_video_inputs(monkeypatch, metadata, error):
    monkeypatch.setattr(worker, "_run", Mock(return_value=Mock(stdout=json.dumps(metadata))))
    with pytest.raises(ValueError, match=error):
        worker.probe_media(Path("source"))


def test_cover_art_is_not_selected_as_the_video_stream(monkeypatch):
    metadata = {"format": {"duration": "2"}, "streams": [
        {"index": 0, "codec_type": "video", "disposition": {"attached_pic": 1}},
        {"index": 2, "codec_type": "video", "width": 640, "height": 360},
    ]}
    run = Mock(return_value=Mock(stdout=json.dumps(metadata)))
    monkeypatch.setattr(worker, "_run", run)
    media = worker.probe_media(Path("source"))
    assert media.video_stream_index == 2
    assert media.duration == 2.0
    assert run.call_args.kwargs["timeout"] == 15


def test_ffmpeg_execution_has_a_hard_timeout_and_no_stdin(monkeypatch):
    run = Mock(side_effect=subprocess.TimeoutExpired(["ffmpeg"], timeout=7))
    monkeypatch.setattr(worker.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="7-second timeout"):
        worker._run(["ffmpeg", "-i", "source"], timeout=7)
    assert run.call_args.kwargs["timeout"] == 7
    assert run.call_args.kwargs["stdin"] == subprocess.DEVNULL


def test_transcode_can_read_local_media_but_cannot_fetch_remote_dependencies():
    media = worker.MediaInfo(duration=2, width=640, height=360, video_stream_index=2)
    command = worker.ffmpeg_command(Path("source"), Path("output.mp4"), "360p", media)
    assert command[command.index("-protocol_whitelist") + 1] == "file,pipe"
    assert command[command.index("-map") + 1] == "0:2"
    assert command[command.index("-t") + 1] == "120"
    assert command[command.index("-map_metadata") + 1] == "-1"


def test_publication_is_fenced_by_current_attempt_and_running_job(config):
    table = Mock()
    worker.record_output(table, config, {"key": "attempt-2.mp4"}, attempt=2)
    request = table.update_item.call_args.kwargs
    assert "attemptCounts.#rendition = :attempt" in request["ConditionExpression"]
    assert "#status = :running" in request["ConditionExpression"]
    assert request["ExpressionAttributeNames"]["#rendition"] == "360p"
    assert request["ExpressionAttributeNames"]["#status"] == "status"
    assert request["ExpressionAttributeValues"][":attempt"] == 2
    assert request["ExpressionAttributeValues"][":running"] == "RUNNING"


def test_attempts_upload_to_distinct_keys_before_conditional_publication(monkeypatch, config):
    s3, session = Mock(), Mock()
    session.client.side_effect = lambda name, **kwargs: s3 if name == "s3" else Mock()
    monkeypatch.setattr(worker.boto3.session, "Session", Mock(return_value=session))
    monkeypatch.setattr(worker, "begin_attempt", Mock(side_effect=[(1, {}), (2, {})]))
    monkeypatch.setattr(worker, "download_source", Mock())
    monkeypatch.setattr(worker, "probe_media", Mock(return_value=worker.MediaInfo(2, 640, 360, 0)))
    monkeypatch.setattr(worker, "record_output", Mock())
    monkeypatch.setattr(worker, "emit_metrics", Mock())

    def produce_output(command, **kwargs):
        Path(command[-1]).write_bytes(b"transcoded-media")

    monkeypatch.setattr(worker, "_run", produce_output)
    assert worker.run_worker(config) == 0
    assert worker.run_worker(config) == 0
    keys = [call.args[2] for call in s3.upload_file.call_args_list]
    assert len(set(keys)) == 2
    assert all(key.startswith(f"jobs/{config.job_id}/{config.rendition}/") for key in keys)


@pytest.mark.parametrize("failure", ["timeout", "empty-output"])
def test_unsuccessful_transcode_exits_nonzero_without_publishing(monkeypatch, config, failure):
    s3, table, cloudwatch = Mock(), Mock(), Mock()
    session = Mock()
    session.client.side_effect = lambda name, **kwargs: {"s3": s3, "cloudwatch": cloudwatch}[name]
    session.resource.return_value.Table.return_value = table
    monkeypatch.setattr(worker.boto3.session, "Session", Mock(return_value=session))
    monkeypatch.setattr(worker, "begin_attempt", Mock(return_value=(1, {})))
    monkeypatch.setattr(worker, "download_source", Mock())
    monkeypatch.setattr(worker, "probe_media", Mock(return_value=worker.MediaInfo(2, 640, 360, 0)))
    processing = Mock(side_effect=RuntimeError("ffmpeg timeout")) if failure == "timeout" else Mock()
    monkeypatch.setattr(worker, "_run", processing)
    events, output, metrics = Mock(), Mock(), Mock()
    monkeypatch.setattr(worker, "append_event", events)
    monkeypatch.setattr(worker, "record_output", output)
    monkeypatch.setattr(worker, "emit_metrics", metrics)

    assert worker.run_worker(config) == 1

    s3.upload_file.assert_not_called()
    output.assert_not_called()
    assert events.call_args.args[2] == "worker_failed"
    assert metrics.call_args.args[2] is False
