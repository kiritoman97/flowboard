from unittest.mock import AsyncMock, patch

from sqlmodel import select

from flowboard.db import get_session
from flowboard.db.models import Board, ExternalLpvVideoJob, Request
from flowboard.routes import lpv_external
from flowboard.services import media as media_service


def test_lpv_i2v_submit_creates_request_and_job(client, monkeypatch):
    async def fake_upload(body):
        assert body.url == "https://example.com/start.png"
        assert body.project_id == "flow-proj-1"
        return {"media_id": "upload-1"}

    with patch("flowboard.routes.lpv_external.get_flow_sdk") as sdk:
        sdk.return_value.create_project = AsyncMock(return_value={"raw": {}, "project_id": "flow-proj-1"})
        monkeypatch.setattr(lpv_external, "upload_image_from_url", fake_upload)
        enqueued = []
        monkeypatch.setattr(lpv_external.get_worker(), "enqueue", lambda rid: enqueued.append(rid))

        res = client.post(
            "/api/external/lpv/i2v",
            json={
                "job_key": "P1_A_S01",
                "run_id": "run-1",
                "product_id": "P1",
                "concept_id": "A",
                "scene_key": "S01",
                "image_url": "https://example.com/start.png",
                "prompt": "move sauce slowly",
            },
        )

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "queued"
    assert body["uploaded_media_id"] == "upload-1"
    assert enqueued == [body["request_id"]]

    with get_session() as s:
        job = s.get(ExternalLpvVideoJob, body["job_id"])
        req = s.get(Request, body["request_id"])
        assert job is not None
        assert req is not None
        assert job.job_key == "P1_A_S01"
        assert req.type == "gen_video"
        assert req.params["start_media_id"] == "upload-1"
        assert req.params["aspect_ratio"] == "VIDEO_ASPECT_RATIO_PORTRAIT"


def test_lpv_i2v_poll_returns_done_and_uploads_storage(client, tmp_path, monkeypatch):
    media_id = "abc123"
    cached = tmp_path / f"{media_id}.mp4"
    cached.write_bytes(b"video")
    monkeypatch.setattr(media_service, "MEDIA_CACHE_DIR", tmp_path)
    monkeypatch.setattr(lpv_external.lpv_storage, "enabled", lambda: True)
    monkeypatch.setattr(
        lpv_external.lpv_storage,
        "upload_file",
        lambda path, key, content_type: {
            "enabled": True,
            "object_key": key,
            "url": "https://s3.example.com/signed",
            "error": None,
        },
    )

    with get_session() as s:
        board = Board(name="LPV")
        s.add(board)
        s.commit()
        s.refresh(board)
        req = Request(
            type="gen_video",
            status="done",
            result={"media_ids": [media_id], "slot_errors": [None]},
        )
        s.add(req)
        s.commit()
        s.refresh(req)
        job = ExternalLpvVideoJob(
            job_key="P1_A_S01",
            run_id="run-1",
            product_id="P1",
            concept_id="A",
            scene_key="S01",
            board_id=board.id,
            flow_project_id="flow-proj-1",
            uploaded_media_id="upload-1",
            request_id=req.id,
        )
        s.add(job)
        s.commit()
        s.refresh(job)
        job_id = job.id

    res = client.get(f"/api/external/lpv/i2v/{job_id}")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "done"
    assert body["flow_media_id"] == media_id
    assert body["flow_media_url"].endswith(f"/media/{media_id}")
    assert body["storage_url"] == "https://s3.example.com/signed"
    assert body["storage_object_key"].endswith("/runs/run-1/videos/P1/A/S01.mp4")


def test_lpv_i2v_cancel_marks_request(client):
    with get_session() as s:
        board = Board(name="LPV")
        s.add(board)
        s.commit()
        s.refresh(board)
        req = Request(type="gen_video", status="queued", result={})
        s.add(req)
        s.commit()
        s.refresh(req)
        job = ExternalLpvVideoJob(job_key="k", board_id=board.id, request_id=req.id)
        s.add(job)
        s.commit()
        s.refresh(job)
        job_id = job.id

    res = client.post(f"/api/external/lpv/i2v/{job_id}/cancel")
    assert res.status_code == 200
    assert res.json()["status"] == "canceled"
    with get_session() as s:
        req = s.exec(select(Request)).first()
        assert req.status == "canceled"
        assert req.error == "canceled"
